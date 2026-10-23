from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """設定值可用環境變數或 backend/.env 覆寫，例如 EASYCOMFY_COMFY_URL=http://192.168.1.10:8188"""

    model_config = SettingsConfigDict(env_prefix="EASYCOMFY_", env_file=BACKEND_DIR / ".env")

    comfy_url: str = "http://127.0.0.1:8188"
    templates_dir: Path = BACKEND_DIR / "templates"
    data_dir: Path = BACKEND_DIR / "data"
    # 單一任務最長執行秒數，超過就中斷並標記失敗（第一次載入 Flux 模型可能要一兩分鐘）
    job_timeout: float = 600
    start_worker: bool = True
    # 中文描述的翻譯：有 Google Cloud Translation 金鑰就用 Google，否則用免費的 MyMemory
    google_api_key: str | None = None
    # 選填：提供 email 給 MyMemory 可把每日免費額度從約 5,000 提高到 50,000 字元
    mymemory_email: str | None = None

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'easycomfy.db').as_posix()}"


settings = Settings()
