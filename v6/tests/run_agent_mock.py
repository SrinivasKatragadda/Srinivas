import os, sys
os.environ.update(NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost", FLOW_SERVER_URL="http://127.0.0.1:9998",
                  AGENT_JOBS_DIR=os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs_ui"), SNOW_VERIFY_TLS="false")
os.environ.setdefault("SNOW_INSTANCE_URL", "https://127.0.0.1:9443")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "snow-ai-service"))
import mock_ai  # patches groq_client
import uvicorn, api
uvicorn.run(api.app, host="127.0.0.1", port=int(os.getenv("AGENT_TEST_PORT", "8011")), log_level="warning")
