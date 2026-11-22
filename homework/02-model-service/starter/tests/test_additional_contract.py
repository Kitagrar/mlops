"""Дополнительные случаи контракта HTTP без загрузки модели."""

import base64
import hashlib
from io import BytesIO
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from model import DEFAULT_ASSETS
from PIL import Image

import service


@pytest.fixture
def client_and_runner(monkeypatch):
    runner = Mock()
    monkeypatch.setattr(service, "ModelRunner", lambda: runner)
    with TestClient(service.app) as client:
        yield client, runner


def test_http_accepts_png_at_pixel_limit(client_and_runner):
    client, runner = client_and_runner
    with BytesIO() as buffer:
        Image.new("RGB", (1024, 1024)).save(buffer, format="PNG")
        image = buffer.getvalue()
    runner.predict.side_effect = lambda value, *, protocol: {
        "caption": "A black square.",
        "model_version": "test",
        "request_id": "png-boundary",
        "image_sha256": hashlib.sha256(value.image).hexdigest(),
    }

    reply = client.post("/predict", json={"image_base64": base64.b64encode(image).decode()})

    assert reply.status_code == 200
    assert reply.json()["image_sha256"] == hashlib.sha256(image).hexdigest()
    assert runner.predict.call_args.args[0].image == image


def test_http_rejects_base64_with_embedded_newline(client_and_runner):
    client, runner = client_and_runner
    image = (DEFAULT_ASSETS / "images/cats.jpg").read_bytes()
    encoded = base64.b64encode(image).decode()
    with_newline = encoded[:4] + "\n" + encoded[4:]

    reply = client.post("/predict", json={"image_base64": with_newline})

    assert reply.status_code == 422
    runner.predict.assert_not_called()
