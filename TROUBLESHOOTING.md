# Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UnicodeEncodeError` printing Devanagari in a Windows console | console codepage is cp1252 | `set PYTHONUTF8=1` (only affects console printing; the API always returns UTF-8 JSON) |
| `/ui/` returns 404 | server started before the `frontend/` dir existed | restart the server; the static mount is evaluated at startup |
| Port 8080 already in use | previous server still running | Windows: `Get-NetTCPConnection -LocalPort 8080` then `Stop-Process`; *nix: `lsof -ti:8080 \| xargs kill` |
| All providers show `unhealthy` except `mock` | no cloud credentials configured (expected offline) | ignore, or add keys in `backend/.env` |
| `provider=azure` still answered by `mock` | Azure has no key → auto fallback | this is correct resilience behaviour; see `data.metadata.fallback` |
| `ModuleNotFoundError: config` when running pytest | invoked outside `backend/` | run from `backend/` (conftest also injects the path) |
| Voice reply doesn't match what was spoken | server fell back to a canned sample (`stt_engine: "sample"` in the response) | install local STT: `pip install faster-whisper` (already in requirements.txt), keep `ENABLE_LOCAL_STT=true`, restart; or let browser speech recognition fill the **Heard** box |
| Record says "mic unavailable" | permission denied / no device / insecure origin — the on-page help box names the exact cause | allow the mic via the address-bar icon in a normal browser tab at `http://127.0.0.1:8080/ui/` (embedded previews can't show the prompt), or use the **📁 Audio file** button — the server transcribes uploads the same way |
| First voice request is slow (~20 s) | whisper model cold load (it warms in the background at startup) | wait for warm-up or pre-download: the model is fetched from Hugging Face on first load |
| `stt_engine` shows `client-stt` instead of `local-whisper` | faster-whisper not installed or `ENABLE_LOCAL_STT=false` | install it / enable the flag; `client-stt` still reflects your real words via browser recognition |
| `google.cloud`/`boto3` import errors when selecting GCP/AWS | optional SDKs not installed | `pip install google-cloud-translate boto3` (see `requirements.txt` comments) |
| Docker build "frontend not found" | built with wrong context | build from repo root: `docker build -f backend/Dockerfile .` |

### Health first
```bash
curl -s localhost:8080/health | jq '.status, .providers'
curl -s localhost:8080/debug  | jq '.provider_configured'
```

### Reset circuit breakers
Restart the process — breaker state is in-memory and per-process.
