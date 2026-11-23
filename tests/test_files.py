"""Uploads: photos, documents, storage isolation, quotas, downloads and the S3 backend."""
import io
import os
import zipfile

import pytest
from PIL import Image
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Learner, LearnerDocument, Role, StoredFile, SubscriptionPlan
from app.storage import storage
from app.tenancy import platform_scope
from tests.conftest import sign_in
from tests.test_learners import BASE, register


def jpeg_with_gps(size=(1200, 900)) -> bytes:
    img = Image.new("RGB", size, (200, 120, 40))
    exif = Image.Exif()
    exif[0x010F] = "SecretPhoneMaker"          # camera make
    exif[0x8825] = {1: "N", 2: (5.0, 33.0, 0.0)}  # GPS info
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def docx(macro=False) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        if macro:
            z.writestr("word/vbaProject.bin", b"\x00evil")
    return out.getvalue()


@pytest.fixture()
def learner(client, org, make_person):
    make_person("tm@attc.example", [Role.TRAINING_MANAGER], org)
    sign_in(client, "tm@attc.example")
    register(client)
    with platform_scope():
        return db.session.scalar(select(Learner).where(Learner.first_name == "Abena"))


def upload_photo(client, l, data, name="me.jpg"):
    return client.post(f"/app/learners/{l.public_id}/photo",
                       data={"photo-photo": (io.BytesIO(data), name)}, content_type="multipart/form-data",
                       follow_redirects=True)


def upload_doc(client, l, data, name="cv.pdf", doc_type="cv", title="My CV"):
    return client.post(f"/app/learners/{l.public_id}/documents",
                       data={"doc-file": (io.BytesIO(data), name), "doc-doc_type": doc_type, "doc-title": title},
                       content_type="multipart/form-data", follow_redirects=True)


def test_photo_is_cleaned_cropped_and_served(client, learner, org):
    r = upload_photo(client, learner, jpeg_with_gps())
    assert b"Passport photo saved" in r.data
    with platform_scope():
        sf = db.session.get(Learner, learner.id).photo
        key = sf.key
    assert key.startswith(f"tenants/{org.public_id}/learner-photos/") and key.endswith(".jpg")
    raw = storage().read(key)
    img = Image.open(io.BytesIO(raw))
    assert img.size == (600, 800) and not img.getexif()
    assert b"SecretPhoneMaker" not in raw
    resp = client.get(f"/app/learners/{learner.public_id}/photo")
    assert resp.status_code == 200 and resp.mimetype == "image/jpeg"
    assert resp.headers["Cache-Control"] == "private, no-store" and resp.headers["X-Content-Type-Options"] == "nosniff"


def test_replacing_photo_deletes_the_old_file(client, learner):
    upload_photo(client, learner, jpeg_with_gps())
    with platform_scope():
        first = db.session.get(Learner, learner.id).photo.key
    upload_photo(client, learner, jpeg_with_gps((800, 800)))
    with pytest.raises(Exception):
        storage().read(first)
    with platform_scope():
        assert StoredFile.query.count() == 1


@pytest.mark.parametrize("data, name, message", [
    (b"<html><script>alert(1)</script></html>", "photo.jpg", b"JPG, PNG or WEBP"),
    (PDF, "photo.png", b"JPG, PNG or WEBP"),
    (b"\xff\xd8\xff\xe0broken", "x.jpg", b"couldn&#39;t be read"),
])
def test_bad_photos_are_refused(client, learner, data, name, message):
    assert message in upload_photo(client, learner, data, name).data


def test_tiny_photo_is_refused(client, learner):
    out = io.BytesIO()
    Image.new("RGB", (60, 80)).save(out, "PNG")
    assert b"too small" in upload_photo(client, learner, out.getvalue(), "tiny.png").data


def test_document_upload_download_and_delete(client, learner):
    r = upload_doc(client, learner, PDF, name="../../etc/passwd.pdf")
    assert b"Document uploaded" in r.data
    with platform_scope():
        doc = LearnerDocument.query.one()
        key, name, pid = doc.file.key, doc.file.original_name, doc.public_id
    assert ".." not in key and name == "etc_passwd.pdf"
    resp = client.get(f"/app/learners/{learner.public_id}/documents/{pid}")
    assert resp.status_code == 200 and resp.data == PDF
    assert resp.headers["Content-Disposition"].startswith("attachment;")
    assert "sandbox" in resp.headers["Content-Security-Policy"]
    with platform_scope():
        assert AuditLog.query.filter_by(action="file.downloaded").count() == 1
    client.post(f"/app/learners/{learner.public_id}/documents/{pid}/delete")
    with pytest.raises(Exception):
        storage().read(key)


@pytest.mark.parametrize("data, name, ok", [
    (PDF, "cert.pdf", True),
    (docx(), "letter.docx", True),
    (docx(macro=True), "letter.docx", False),
    (b"MZ\x90\x00executable", "cv.pdf", False),
    (b"<html><body>hi</body></html>", "cv.pdf", False),
    (b"PK\x03\x04not really a zip", "cv.docx", False),
    (b"", "empty.pdf", False),
])
def test_document_types_are_checked_by_content(client, learner, data, name, ok):
    r = upload_doc(client, learner, data, name=name)
    assert (b"Document uploaded" in r.data) is ok


def test_storage_quota(client, learner):
    with platform_scope():
        db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "org-professional")).storage_mb = 1
        db.session.commit()
    big = PDF + b"0" * (700 * 1024)
    assert b"Document uploaded" in upload_doc(client, learner, big).data
    r = upload_doc(client, learner, big, name="second.pdf")
    assert b"Your plan allows 1 storage" in r.data
    with platform_scope():
        assert StoredFile.query.count() == 1  # nothing half-saved


def test_file_size_limit(app, client, learner):
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024
    r = upload_doc(client, learner, PDF + b"0" * (60 * 1024))
    assert r.status_code == 413


def test_documents_isolated_between_workspaces(app, client, learner, make_tenant, make_person):
    upload_doc(client, learner, PDF)
    with platform_scope():
        pid = LearnerDocument.query.one().public_id
    other = make_tenant("Beta NGO", organization_type="ngo")
    make_person("b@beta.example", [Role.ORG_ADMIN], other)
    c2 = app.test_client()
    sign_in(c2, "b@beta.example")
    assert c2.get(f"/app/learners/{learner.public_id}/documents/{pid}").status_code == 404


def test_permanent_delete_erases_files(client, org, make_person, learner):
    upload_photo(client, learner, jpeg_with_gps())
    upload_doc(client, learner, PDF)
    with platform_scope():
        keys = [sf.key for sf in StoredFile.query]
    r = client.post(f"/app/learners/{learner.public_id}/delete", data={"del-confirm": learner.learner_no})
    assert r.status_code == 403  # training managers can't erase records
    client.post("/signout")
    make_person("admin@attc.example", [Role.ORG_ADMIN], org)
    sign_in(client, "admin@attc.example")
    r = client.post(f"/app/learners/{learner.public_id}/delete", data={"del-confirm": "wrong"}, follow_redirects=True)
    assert b"Type the learner number exactly" in r.data
    r = client.post(f"/app/learners/{learner.public_id}/delete", data={"del-confirm": learner.learner_no.lower()},
                    follow_redirects=True)
    assert b"permanently deleted" in r.data
    with platform_scope():
        assert Learner.query.count() == 0 and StoredFile.query.count() == 0
    for key in keys:
        with pytest.raises(Exception):
            storage().read(key)


def test_local_storage_refuses_path_escape(app):
    from app.storage import LocalStorage, StorageError
    s = LocalStorage(app.config["UPLOAD_ROOT"])
    with pytest.raises(StorageError):
        s.save("../../outside.txt", b"x", "text/plain")


def test_s3_backend(tmp_path):
    moto = pytest.importorskip("moto")
    import boto3

    from app import create_app
    from app.storage import S3Storage
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="portia-test")
        app = create_app("testing", {"STORAGE_BACKEND": "s3", "STORAGE_BUCKET": "portia-test",
                                     "STORAGE_REGION": "us-east-1"})
        with app.app_context():
            s = storage()
            assert isinstance(s, S3Storage)
            s.save("tenants/x/doc.pdf", PDF, "application/pdf")
            assert s.read("tenants/x/doc.pdf") == PDF
            s.delete("tenants/x/doc.pdf")
            with pytest.raises(Exception):
                s.read("tenants/x/doc.pdf")
