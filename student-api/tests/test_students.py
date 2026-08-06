from uuid import UUID

BASE_URL = "/api/v1/students"


# ---------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------

def test_create_student_success(client, student_payload):
    response = client.post(BASE_URL, json=student_payload)

    assert response.status_code == 201

    body = response.json()
    assert body["first_name"] == student_payload["first_name"]
    assert body["last_name"] == student_payload["last_name"]
    assert body["email"] == student_payload["email"]
    assert body["age"] == student_payload["age"]

    # id must be a valid, version-7 UUID (time-ordered, index-friendly).
    student_id = UUID(body["id"])
    assert student_id.version == 7


def test_create_student_duplicate_email_returns_409(client, student_payload):
    first = client.post(BASE_URL, json=student_payload)
    assert first.status_code == 201

    second = client.post(BASE_URL, json=student_payload)
    assert second.status_code == 409


def test_create_student_invalid_payload_returns_422(client):
    response = client.post(
        BASE_URL,
        json={
            "first_name": "A",  # below min_length=2
            "last_name": "Lovelace",
            "email": "not-an-email",
            "age": 0,  # below ge=1
        },
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------
# Read (list)
# ---------------------------------------------------------------------

def test_get_all_students_empty(client):
    response = client.get(BASE_URL)

    assert response.status_code == 200
    assert response.json() == []


def test_get_all_students_returns_created_students(client, student_payload):
    client.post(BASE_URL, json=student_payload)

    second_payload = {**student_payload, "email": "second@example.com"}
    client.post(BASE_URL, json=second_payload)

    response = client.get(BASE_URL)

    assert response.status_code == 200
    assert len(response.json()) == 2


# ---------------------------------------------------------------------
# Read (single)
# ---------------------------------------------------------------------

def test_get_student_by_id_success(client, student_payload):
    created = client.post(BASE_URL, json=student_payload).json()

    response = client.get(f"{BASE_URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_student_by_id_not_found(client):
    random_id = "018f9a8e-2222-7000-8000-000000000000"

    response = client.get(f"{BASE_URL}/{random_id}")

    assert response.status_code == 404


def test_get_student_by_invalid_id_returns_422(client):
    response = client.get(f"{BASE_URL}/not-a-uuid")

    assert response.status_code == 422


# ---------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------

def test_update_student_success(client, student_payload):
    created = client.post(BASE_URL, json=student_payload).json()

    response = client.put(
        f"{BASE_URL}/{created['id']}",
        json={"age": 29},
    )

    assert response.status_code == 200

    body = response.json()
    assert body["age"] == 29
    # Unspecified fields should remain untouched.
    assert body["first_name"] == student_payload["first_name"]


def test_update_student_not_found(client):
    random_id = "018f9a8e-2222-7000-8000-000000000000"

    response = client.put(
        f"{BASE_URL}/{random_id}",
        json={"age": 30},
    )

    assert response.status_code == 404


def test_update_student_duplicate_email_returns_409(client, student_payload):
    first = client.post(BASE_URL, json=student_payload).json()

    second_payload = {**student_payload, "email": "second@example.com"}
    second = client.post(BASE_URL, json=second_payload).json()

    response = client.put(
        f"{BASE_URL}/{second['id']}",
        json={"email": first["email"]},
    )

    assert response.status_code == 409


# ---------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------

def test_delete_student_success(client, student_payload):
    created = client.post(BASE_URL, json=student_payload).json()

    response = client.delete(f"{BASE_URL}/{created['id']}")
    assert response.status_code == 204

    follow_up = client.get(f"{BASE_URL}/{created['id']}")
    assert follow_up.status_code == 404


def test_delete_student_not_found(client):
    random_id = "018f9a8e-2222-7000-8000-000000000000"

    response = client.delete(f"{BASE_URL}/{random_id}")

    assert response.status_code == 404