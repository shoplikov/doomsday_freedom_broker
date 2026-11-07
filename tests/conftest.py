import os
import tempfile

# Must run before any `cqa` import: isolated DB, mock LLM, offline data.
_tmp = tempfile.mkdtemp()
os.environ["CQA_DB_PATH"] = os.path.join(_tmp, "test.sqlite3")
os.environ["CQA_LOG_DIR"] = os.path.join(_tmp, "logs")
os.environ["LLM_PROVIDER"] = "mock"
os.environ["DATASET_API_URL"] = ""
