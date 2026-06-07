"""Google Sheets (опционально). Подключается, если задан GOOGLE_CREDS_FILE
и администратор ввёл ссылку на таблицу. Иначе тихо отключено."""
from config import GOOGLE_CREDS_FILE
from database import crud

try:
    import gspread
    from google.oauth2.service_account import Credentials
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


async def _client():
    if not _AVAILABLE or not GOOGLE_CREDS_FILE:
        return None
    url = await crud.get_setting("gsheet_url")
    if not url:
        return None
    try:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)
        gc = gspread.authorize(creds)
        return gc.open_by_url(url)
    except Exception:
        return None


async def append_submission(name: str, group: str, when: str, status: str) -> None:
    """Дублирует сдачу в Google Sheets, если настроено."""
    sh = await _client()
    if sh is None:
        return
    try:
        ws = sh.worksheet("Сдачи")
        ws.append_row([name, group, when, status])
        if "просроч" in status.lower():
            try:
                late_ws = sh.worksheet("Просроченные")
            except Exception:
                late_ws = sh.add_worksheet("Просроченные", rows=100, cols=4)
            late_ws.append_row([name, group, when, status])
    except Exception:
        pass
