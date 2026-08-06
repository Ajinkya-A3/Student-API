def test_liveness_probe(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_readiness_probe(client):
    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "connected",
    }


def test_root_endpoint(client):
    response = client.get("/")

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "running"
    assert "service" in body
    assert "version" in body