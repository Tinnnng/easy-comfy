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

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'easycomfy.db').as_posix()}"


settings = Settings()
