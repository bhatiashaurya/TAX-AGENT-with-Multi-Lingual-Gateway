"""Integration tests for the HTTP API via FastAPI TestClient."""


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("healthy", "degraded")
    # voice STT providers moved under "voice_providers" when chat became primary
    assert body["voice_providers"]["mock"]["status"] == "healthy"


def test_text_endpoint_hinglish(client, sample_queries):
    for q in sample_queries:
        r = client.post("/api/text", json={"text": q["text"], "provider": "mock"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["input"]["language_detected"]["code"] == q["lang"]
        assert data["processing"]["intent_extraction"]["intent"] == q["intent"]
        assert data["processing"]["intent_extraction"]["tax_type"] == q["tax"]
        if q["preserve"]:
            assert q["preserve"] in data["processing"]["normalization"]["terminology_preserved"]


def test_text_validation_too_long(client):
    r = client.post("/api/text", json={"text": "x" * 1001})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_multi_turn_session(client):
    r1 = client.post("/api/text", json={"text": "मेरा GST refund kahan hai"})
    session_id = r1.json()["data"]["agent_response"]["session_id"]
    assert session_id
    # Turn 2: provide a GSTIN in the same session -> agent should "resolve".
    r2 = client.post("/api/text", json={
        "text": "मेरा GSTIN 27ABCDE1234F1Z5 hai", "session_id": session_id,
    })
    agent = r2.json()["data"]["agent_response"]
    assert agent["session_id"] == session_id
    assert agent.get("reference_number") == "27ABCDE1234F1Z5"


def test_provider_fallback_to_mock(client):
    # Azure is unconfigured offline; request should transparently fall back.
    r = client.post("/api/text", json={
        "text": "मेरा GST refund status check karo", "provider": "azure",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success_with_fallback"
    assert body["data"]["metadata"]["provider_used"] == "mock"
    assert body["data"]["metadata"]["fallback_used"] is True
    chain = body["data"]["metadata"]["fallback"]["fallback_chain"]
    assert chain[0]["provider"] == "azure" and chain[0]["status"] == "failed"
    assert chain[-1]["provider"] == "mock" and chain[-1]["status"] == "success"


def test_provider_switch(client):
    r = client.post("/api/provider/switch", json={
        "new_provider": "gcp", "make_default": False, "test_connectivity": True,
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["current_provider"] == "gcp"
    assert data["is_default"] is False


def test_unknown_provider_rejected(client):
    # "ibm" fails the Literal enum on the request model -> global 400 handler.
    r = client.post("/api/provider/switch", json={"new_provider": "ibm"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_voice_endpoint(client, sample_wav_bytes):
    files = {"audio": ("recording.wav", sample_wav_bytes, "audio/wav")}
    data = {"provider": "mock", "return_audio": "true",
            "mock_transcript": "Mera TDS correction request ka status batao"}
    r = client.post("/api/voice", files=files, data=data)
    assert r.status_code == 200
    payload = r.json()["data"]
    assert payload["transcription"]["transcript"].startswith("Mera TDS")
    assert "TDS" in payload["normalization"]["terminology_preserved"]
    assert payload["intent_extraction"]["intent"] == "CORRECTION"
    assert payload["response_audio"]["data"].startswith("data:audio/wav;base64,")
    assert payload["audio_file_info"]["format"] == "wav"


def test_voice_transcript_hint_survives_fallback(client, sample_wav_bytes):
    # An unconfigured cloud provider falls back to mock STT; the client-side
    # transcript must still win so the reply matches the words actually spoken.
    files = {"audio": ("recording.webm", sample_wav_bytes, "audio/webm")}
    data = {"provider": "azure",
            "mock_transcript": "PAN verification chahiye"}
    r = client.post("/api/voice", files=files, data=data)
    assert r.status_code == 200
    payload = r.json()["data"]
    assert payload["transcription"]["transcript"] == "PAN verification chahiye"
    assert payload["transcription"]["stt_engine"] == "client-stt"
    assert "PAN" in payload["normalization"]["terminology_preserved"]


def test_voice_without_hint_uses_canned_sample(client, sample_wav_bytes):
    # No local STT (disabled in tests) and no client transcript: the mock falls
    # back to a canned sample and says so via the engine field.
    files = {"audio": ("recording.wav", sample_wav_bytes, "audio/wav")}
    r = client.post("/api/voice", files=files, data={"provider": "mock"})
    assert r.status_code == 200
    payload = r.json()["data"]
    assert payload["transcription"]["stt_engine"] == "sample"
    assert payload["transcription"]["transcript"]  # non-empty canned text


def test_metrics_endpoint(client):
    client.post("/api/text", json={"text": "PAN verification chahiye"})
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["requests"]["total"] >= 1
    assert "mock" in body["providers"]
