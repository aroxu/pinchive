"""Lightweight i18n — dict catalogs + a per-request locale ContextVar.

No Babel/gettext toolchain (keeps the low-footprint promise): templates call
`t('key')`, the middleware sets the locale per request, and `t` reads it from a
ContextVar. Locale resolution order: ?lang= > `lang` cookie > Accept-Language >
DEFAULT. Add a language by extending SUPPORTED + the catalogs.
"""

from __future__ import annotations

import contextvars

DEFAULT = "en"
SUPPORTED = ("en", "ko")
LANG_NAMES = {"en": "EN", "ko": "한국어"}

_current: contextvars.ContextVar[str] = contextvars.ContextVar("locale", default=DEFAULT)


def set_locale(loc: str) -> None:
    _current.set(loc if loc in SUPPORTED else DEFAULT)


def get_locale() -> str:
    return _current.get()


def t(key: str, **kw) -> str:
    loc = _current.get()
    s = TRANSLATIONS.get(loc, {}).get(key)
    if s is None:
        s = TRANSLATIONS[DEFAULT].get(key, key)
    return s.format(**kw) if kw else s


def resolve_locale(request) -> str:
    q = request.query_params.get("lang")
    if q in SUPPORTED:
        return q
    c = request.cookies.get("lang")
    if c in SUPPORTED:
        return c
    for part in request.headers.get("accept-language", "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in SUPPORTED:
            return code
    return DEFAULT


EN = {
    # nav / footer
    "nav.boards": "Boards",
    "nav.duplicates": "Duplicates",
    "nav.credentials": "Credentials",
    "nav.settings": "Settings",
    "nav.add_board": "Add board",
    "sidebar.autosync": "Auto-sync active",
    "sidebar.syncing": "Syncing",
    "sidebar.n_boards": "{n} boards",
    "sidebar.n_sessions": "{n} sessions",
    "sidebar.auth_note": "Cookie auth",
    # settings page
    "settings.title": "Settings",
    "settings.automation": "Automation",
    "settings.env_note": "These are set via environment (.env) and take effect on restart.",
    "settings.resync_interval": "Board re-sync interval",
    "settings.refresh_interval": "Credential keep-alive interval",
    "settings.dedup_interval": "Duplicate scan interval",
    "settings.concurrency": "Concurrent downloads",
    "settings.timeout": "Per-pin stall timeout",
    "settings.page_sizes": "Page sizes (boards / pins / dupes)",
    "settings.playwright": "Playwright re-login fallback",
    "settings.queue": "Task queue",
    "settings.every_hours": "every {n}h",
    "settings.disabled": "disabled",
    "settings.enabled": "enabled",
    "settings.on": "connected",
    "settings.off": "unavailable",
    "settings.sync_all": "Sync all boards now",
    "settings.synced": "Queued a re-sync of all boards.",
    "settings.hours_unit": "{n}h",
    "settings.seconds_unit": "{n}s",
    "settings.save": "Save",
    "settings.saved": "Settings saved.",
    "settings.zero_disables": "0 = disabled",
    "settings.dl_sleep": "Delay between requests (s)",
    "settings.per_page_boards": "Boards per page",
    "settings.per_page_pins": "Pins per page",
    "settings.per_page_dupes": "Duplicate groups per page",
    "settings.restart_note": "restart to change",
    "settings.cron_help_title": "Schedules use crontab syntax",
    "settings.cron_help": (
        "field order:  minute  hour  day-of-month  month  day-of-week\n"
        "  *  = any     */n = every n     a-b = range     a,b = list\n"
        "day-of-week: 0-6 (Sun = 0)\n"
        "examples:  0 */6 * * * (every 6h)  ·  30 4 * * * (daily 04:30)  ·  "
        "0 3 * * 1 (Mon 03:00)"
    ),
    "settings.cron_disabled_hint": "empty = disabled",
    "settings.schedules": "Schedules",
    "settings.g_download": "Download",
    "settings.g_display": "Display · page sizes",
    "settings.g_fallback": "Session fallback",
    "settings.concurrency_help": (
        "How many boards the worker downloads in parallel. This is a startup "
        "setting: set PINCHIVE_MAX_CONCURRENCY in your .env and restart the worker."
    ),
    "settings.concurrency_env": "set in .env (PINCHIVE_MAX_CONCURRENCY) · restart to apply",
    "settings.resync_desc": "Auto re-download boards to pick up newly added pins. Empty = off.",
    "settings.refresh_desc": "Keep session cookies alive so private boards keep working. Empty = off.",
    "settings.dedup_desc": "Recompute and store duplicate-image groups. Empty = off.",
    "settings.timeout_desc": "Skip a single pin if it stalls with no data this long (seconds).",
    "settings.dl_sleep_desc": "Delay between download requests to avoid rate limiting (seconds).",
    "settings.per_page_boards_desc": "Boards shown per page.",
    "settings.per_page_pins_desc": "Pins shown per page.",
    "settings.per_page_dupes_desc": "Duplicate groups shown per page.",
    "settings.playwright_desc": "When a session is truly dead, try a headless-browser "
                               "re-login (needs the :playwright image).",
    "footer.tagline": "Self-hosted Pinterest board archiver · inspired by TubeArchivist",
    # hero
    "hero.badge": "Self-hosted",
    "hero.title_1": "Archive any",
    "hero.title_2": "Pinterest board.",
    "hero.subtitle": "Paste a board URL. Pinchive pulls every pin — images and "
                     "video — to your own disk. Private boards too, with your "
                     "session cookies.",
    "stat.boards": "boards",
    "stat.pins": "pins",
    "stat.sessions": "sessions",
    # add form
    "add.title": "add board",
    "add.auto": "Auto authentication",
    "add.auto_desc": "Try public first; if blocked, retry with a valid session automatically.",
    "add.pick_session": "Select a session",
    "form.board_url": "Board URL",
    "form.session": "Session (for private boards)",
    "form.public_option": "Public — no login",
    "form.expired_suffix": " (expired)",
    "btn.download_board": "Download board",
    # archive list
    "archive.title": "Your archive",
    "archive.count": "{n} boards",
    "filter.search_boards": "Search boards…",
    "filter.all_status": "All status",
    "filter.all_tags": "All tags",
    "btn.filter": "Filter",
    "btn.clear": "Clear",
    "empty.no_boards_title": "No boards yet.",
    "empty.no_boards_body": "Add a Pinterest board URL above to start archiving.",
    "empty.no_matching_boards_title": "No matching boards.",
    "empty.try_clear": "Try clearing filters.",
    # board status (user-facing is just working/done; the rest are internal)
    "status.working": "working",
    "status.done": "done",
    "status.downloading": "downloading",
    "status.queued": "queued",
    "status.pending": "waiting",
    "status.waiting": "waiting",
    "status.partial": "partial",
    "status.error": "error",
    "status.unchecked": "unchecked",
    "status.active": "active",
    "status.expired": "expired",
    # board card
    "count.downloaded": "downloaded",
    "count.skipped": "skipped",
    "count.errors": "errors",
    "btn.view_pins": "View pins",
    "btn.resync": "Re-sync",
    "btn.delete": "Delete",
    "autosync.on": "auto-sync on",
    "autosync.off": "auto-sync off",
    "autosync.title": "Include this board in the periodic auto-resync",
    "tag.add": "+ tag",
    "confirm.delete_board": "Delete this board and all its downloaded files from disk?",
    # cta
    "cta.title": "Keep sessions fresh.",
    "cta.body": "Private boards need valid cookies. Pinchive re-checks them on a "
                "schedule and flags any that expire — so a private archive never "
                "silently stops.",
    "btn.manage_credentials": "Manage credentials",
    # board detail
    "detail.back": "← Boards",
    "stat.pins_on_disk": "saved pins",
    "stat.matching_pins": "matching pins",
    "log.title": "gallery-dl · log tail",
    "filter.search_pins": "Search pins (title, filename, source)…",
    "media.all": "All media",
    "media.images": "Images",
    "media.videos": "Videos",
    "sort.newest": "Newest",
    "sort.oldest": "Oldest",
    "sort.largest": "Largest",
    "sort.smallest": "Smallest",
    "sort.name": "Name",
    "view.small": "Small",
    "view.medium": "Medium",
    "view.large": "Large",
    "filter.dupes_only": "duplicates only",
    "bulk.select_all": "select all",
    "bulk.selected": "{n} selected",
    "bulk.tag_name": "tag name",
    "bulk.add_tag": "Add tag",
    "bulk.remove_tag": "Remove tag",
    "bulk.delete_selected": "Delete selected",
    "confirm.bulk_delete": "Delete the selected pins from disk?",
    "alert.select_pins": "Select some pins first.",
    "empty.no_media_title": "No media yet.",
    "empty.no_matching_pins_title": "No matching pins.",
    "empty.downloading": "Download in progress — check back shortly.",
    "empty.no_media_body": "Nothing downloaded. Try Re-sync, or check the log above.",
    # credentials
    "creds.badge": "Sessions",
    "creds.title": "Credentials",
    "creds.subtitle": "Private boards use your Pinterest session cookies. Paste an "
                      "exported cookies.txt (Netscape) or a JSON cookie array. "
                      "Pinchive re-validates them on a schedule and flags expiry.",
    "creds.how_export": "how to export",
    "creds.export_1": "# 1. Log in to pinterest.com in your browser",
    "creds.export_2": "# 2. Use a \"Get cookies.txt\" extension",
    "creds.export_3": "# 3. Export cookies for pinterest.com",
    "creds.export_4": "# 4. Paste the file contents on the right →",
    "creds.export_req": "required cookie",
    "creds.accepted": "Netscape or JSON both accepted",
    "form.name": "Name",
    "form.cookies": "Cookies",
    "btn.save_validate": "Save & validate",
    "creds.stored": "Stored sessions",
    "creds.count": "{n} sessions",
    "creds.empty": "No credentials stored. Public boards don't need one.",
    "th.name": "Name",
    "th.status": "Status",
    "th.last_checked": "Last checked",
    "th.note": "Note",
    "btn.validate": "Validate",
    "confirm.delete_credential": "Delete this credential and its cookies?",
    # duplicates
    "dup.badge": "Dedup",
    "dup.title": "Duplicate images",
    "dup.subtitle_1": "Same picture across pins — exact byte matches and visually "
                      "identical re-encodes (perceptual hash). The highest-"
                      "resolution copy in each group is marked",
    "dup.subtitle_2": "; the rest are pre-selected for deletion.",
    "dup.removable": "removable copies",
    "dup.copies": "{n} copies",
    "dup.keep": "keep",
    "dup.rescan": "Rescan",
    "dup.rescan_started": "Rescanning in the background — refresh in a moment.",
    "dup.delete_all": "Delete all duplicates",
    "confirm.dup_delete_all": "Keep only the highest-resolution copy in every group "
                              "and delete all other copies from disk?",
    "dup.status_queued": "Rescan queued…",
    "dup.status_hashing": "Hashing images {cur}/{total}…",
    "dup.status_grouping": "Grouping duplicates…",
    "dup.status_done": "Scan complete · {groups} groups · {removable} removable",
    "dup.empty_title": "No duplicates found.",
    "dup.empty_body": "Every archived image is unique — nothing to clean up.",
    "dup.board": "board {id}",
    "confirm.dupe_delete": "Delete the selected copies? Files are removed from disk.",
    # pagination
    "pager.prev": "← Prev",
    "pager.next": "Next →",
    "pager.page": "Page {p} / {pages}",
}

KO = {
    "nav.boards": "보드",
    "nav.duplicates": "중복",
    "nav.credentials": "인증정보",
    "nav.settings": "설정",
    "nav.add_board": "보드 추가",
    "sidebar.autosync": "자동 동기화 활성",
    "sidebar.syncing": "동기화 중",
    "sidebar.n_boards": "{n}개 보드",
    "sidebar.n_sessions": "{n}개 세션",
    "sidebar.auth_note": "쿠키 인증",
    "settings.title": "설정",
    "settings.automation": "자동화",
    "settings.env_note": "환경변수(.env)로 설정하며 재시작 시 적용됩니다.",
    "settings.resync_interval": "보드 재동기화 주기",
    "settings.refresh_interval": "인증정보 keep-alive 주기",
    "settings.dedup_interval": "중복 검사 주기",
    "settings.concurrency": "동시 다운로드 수",
    "settings.timeout": "핀 stall 제한시간",
    "settings.page_sizes": "페이지 크기 (보드 / 핀 / 중복)",
    "settings.playwright": "Playwright 재로그인 fallback",
    "settings.queue": "작업 큐",
    "settings.every_hours": "{n}시간마다",
    "settings.disabled": "비활성",
    "settings.enabled": "활성",
    "settings.on": "연결됨",
    "settings.off": "사용 불가",
    "settings.sync_all": "모든 보드 지금 동기화",
    "settings.synced": "모든 보드 재동기화를 큐에 넣었습니다.",
    "settings.hours_unit": "{n}시간",
    "settings.seconds_unit": "{n}초",
    "settings.save": "저장",
    "settings.saved": "설정을 저장했습니다.",
    "settings.zero_disables": "0 = 비활성",
    "settings.dl_sleep": "요청 간 지연(초)",
    "settings.per_page_boards": "페이지당 보드",
    "settings.per_page_pins": "페이지당 핀",
    "settings.per_page_dupes": "페이지당 중복 그룹",
    "settings.restart_note": "변경은 재시작 필요",
    "settings.cron_help_title": "주기는 crontab 문법을 사용합니다",
    "settings.cron_help": (
        "필드 순서:  분  시  일  월  요일\n"
        "  *  = 전체     */n = n 마다     a-b = 범위     a,b = 목록\n"
        "요일: 0-6 (일요일 = 0)\n"
        "예:  0 */6 * * * (6시간마다)  ·  30 4 * * * (매일 04:30)  ·  "
        "0 3 * * 1 (월 03:00)"
    ),
    "settings.cron_disabled_hint": "빈 값 = 비활성",
    "settings.schedules": "예약",
    "settings.g_download": "다운로드",
    "settings.g_display": "표시 · 페이지 크기",
    "settings.g_fallback": "세션 폴백",
    "settings.concurrency_help": (
        "워커가 동시에 다운로드하는 보드 수입니다. 시작 시 설정값이라 .env 의 "
        "PINCHIVE_MAX_CONCURRENCY 를 바꾸고 워커를 재시작해야 적용됩니다."
    ),
    "settings.concurrency_env": ".env 에서 설정 (PINCHIVE_MAX_CONCURRENCY) · 재시작 필요",
    "settings.resync_desc": "새로 추가된 핀을 가져오도록 보드를 자동 재다운로드. 빈 값 = 비활성.",
    "settings.refresh_desc": "비공개 보드가 계속 동작하도록 세션 쿠키를 유지. 빈 값 = 비활성.",
    "settings.dedup_desc": "중복 이미지 그룹을 다시 계산해 저장. 빈 값 = 비활성.",
    "settings.timeout_desc": "한 핀이 이 시간(초) 동안 데이터 없이 멈추면 건너뜀.",
    "settings.dl_sleep_desc": "Ratelimit 방지를 위한 다운로드 요청 간의 딜레이(초).",
    "settings.per_page_boards_desc": "페이지당 표시할 보드 수.",
    "settings.per_page_pins_desc": "페이지당 표시할 핀 수.",
    "settings.per_page_dupes_desc": "페이지당 표시할 중복 그룹 수.",
    "settings.playwright_desc": "세션이 완전히 죽으면 헤드리스 브라우저로 재로그인 시도"
                               "(:playwright 이미지 필요).",
    "footer.tagline": "셀프호스팅 Pinterest 보드 아카이버 · TubeArchivist 에서 영감",
    "hero.badge": "셀프호스팅",
    "hero.title_1": "어떤 Pinterest",
    "hero.title_2": "보드든 저장.",
    "hero.subtitle": "보드 URL 만 붙여넣으세요. Pinchive 가 모든 핀(이미지·비디오)을 "
                     "내 디스크로 가져옵니다. 세션 쿠키로 비공개 보드도.",
    "stat.boards": "보드",
    "stat.pins": "핀",
    "stat.sessions": "세션",
    "add.title": "보드 추가",
    "add.auto": "자동 인증",
    "add.auto_desc": "공개로 먼저 시도하고, 막히면 유효한 세션으로 자동 재시도해요.",
    "add.pick_session": "인증 세션 선택",
    "form.board_url": "보드 URL",
    "form.session": "세션 (비공개 보드용)",
    "form.public_option": "공개 — 로그인 불필요",
    "form.expired_suffix": " (만료됨)",
    "btn.download_board": "보드 다운로드",
    "archive.title": "내 아카이브",
    "archive.count": "보드 {n}개",
    "filter.search_boards": "보드 검색…",
    "filter.all_status": "모든 상태",
    "filter.all_tags": "모든 태그",
    "btn.filter": "필터",
    "btn.clear": "초기화",
    "empty.no_boards_title": "아직 보드가 없어요.",
    "empty.no_boards_body": "위에 Pinterest 보드 URL 을 추가해 시작하세요.",
    "empty.no_matching_boards_title": "일치하는 보드가 없어요.",
    "empty.try_clear": "필터를 초기화해 보세요.",
    "status.working": "작업중",
    "status.done": "완료",
    "status.downloading": "다운로드 중",
    "status.queued": "대기열",
    "status.pending": "대기 중",
    "status.waiting": "대기 중",
    "status.partial": "부분완료",
    "status.error": "오류",
    "status.unchecked": "미확인",
    "status.active": "활성",
    "status.expired": "만료됨",
    "count.downloaded": "다운로드됨",
    "count.skipped": "건너뜀",
    "count.errors": "오류",
    "btn.view_pins": "핀 보기",
    "btn.resync": "재동기화",
    "btn.delete": "삭제",
    "autosync.on": "자동동기화 켜짐",
    "autosync.off": "자동동기화 꺼짐",
    "autosync.title": "이 보드를 주기적 자동 재동기화에 포함",
    "tag.add": "+ 태그",
    "confirm.delete_board": "이 보드와 다운로드된 모든 파일을 디스크에서 삭제할까요?",
    "cta.title": "세션을 항상 유효하게.",
    "cta.body": "비공개 보드는 유효한 쿠키가 필요합니다. Pinchive 가 주기적으로 확인하고 "
                "만료를 표시해, 비공개 아카이브가 조용히 멈추지 않게 합니다.",
    "btn.manage_credentials": "인증정보 관리",
    "detail.back": "← 보드",
    "stat.pins_on_disk": "저장된 핀",
    "stat.matching_pins": "일치하는 핀",
    "log.title": "gallery-dl · 로그",
    "filter.search_pins": "핀 검색 (제목·파일명·출처)…",
    "media.all": "모든 미디어",
    "media.images": "이미지",
    "media.videos": "비디오",
    "sort.newest": "최신순",
    "sort.oldest": "오래된순",
    "sort.largest": "큰 순",
    "sort.smallest": "작은 순",
    "sort.name": "이름순",
    "view.small": "작게",
    "view.medium": "보통",
    "view.large": "크게",
    "filter.dupes_only": "중복만",
    "bulk.select_all": "전체 선택",
    "bulk.selected": "{n}개 선택됨",
    "bulk.tag_name": "태그 이름",
    "bulk.add_tag": "태그 추가",
    "bulk.remove_tag": "태그 제거",
    "bulk.delete_selected": "선택 삭제",
    "confirm.bulk_delete": "선택한 핀을 디스크에서 삭제할까요?",
    "alert.select_pins": "핀을 먼저 선택하세요.",
    "empty.no_media_title": "아직 미디어가 없어요.",
    "empty.no_matching_pins_title": "일치하는 핀이 없어요.",
    "empty.downloading": "다운로드 진행 중 — 잠시 후 확인하세요.",
    "empty.no_media_body": "다운로드된 것이 없어요. 재동기화하거나 위 로그를 확인하세요.",
    "creds.badge": "세션",
    "creds.title": "인증정보",
    "creds.subtitle": "비공개 보드는 Pinterest 세션 쿠키를 사용합니다. 내보낸 "
                      "cookies.txt(Netscape) 또는 JSON 쿠키 배열을 붙여넣으세요. "
                      "Pinchive 가 주기적으로 재검증하고 만료를 표시합니다.",
    "creds.how_export": "내보내는 법",
    "creds.export_1": "# 1. 브라우저에서 pinterest.com 로그인",
    "creds.export_2": "# 2. \"Get cookies.txt\" 확장 프로그램 사용",
    "creds.export_3": "# 3. pinterest.com 쿠키 내보내기",
    "creds.export_4": "# 4. 파일 내용을 오른쪽에 붙여넣기 →",
    "creds.export_req": "필수 쿠키",
    "creds.accepted": "Netscape · JSON 모두 가능",
    "form.name": "이름",
    "form.cookies": "쿠키",
    "btn.save_validate": "저장 & 검증",
    "creds.stored": "저장된 세션",
    "creds.count": "세션 {n}개",
    "creds.empty": "저장된 인증정보가 없어요. 공개 보드는 필요 없습니다.",
    "th.name": "이름",
    "th.status": "상태",
    "th.last_checked": "마지막 확인",
    "th.note": "비고",
    "btn.validate": "검증",
    "confirm.delete_credential": "이 인증정보와 쿠키를 삭제할까요?",
    "dup.badge": "중복제거",
    "dup.title": "중복 이미지",
    "dup.subtitle_1": "여러 핀에 걸친 같은 이미지 — 바이트 동일 + 시각적으로 같은 "
                      "재인코딩(퍼셉추얼 해시). 각 그룹의 최고해상도 사본을",
    "dup.subtitle_2": "으로 표시하고, 나머지는 삭제 대상으로 미리 선택합니다.",
    "dup.removable": "제거 가능 사본",
    "dup.copies": "사본 {n}개",
    "dup.keep": "유지",
    "dup.rescan": "재검사",
    "dup.rescan_started": "백그라운드에서 재검사 중 — 잠시 후 새로고침하세요.",
    "dup.delete_all": "모든 중복 삭제",
    "confirm.dup_delete_all": "모든 그룹에서 최고 해상도 1장만 남기고 나머지 사본을 "
                             "디스크에서 전부 삭제할까요?",
    "dup.status_queued": "재검사 대기 중…",
    "dup.status_hashing": "이미지 해시 계산 {cur}/{total}…",
    "dup.status_grouping": "중복 그룹화 중…",
    "dup.status_done": "검사 완료 · 그룹 {groups}개 · 제거 가능 {removable}개",
    "dup.empty_title": "중복이 없어요.",
    "dup.empty_body": "모든 이미지가 고유합니다 — 정리할 것이 없어요.",
    "dup.board": "보드 {id}",
    "confirm.dupe_delete": "선택한 사본을 삭제할까요? 파일이 디스크에서 제거됩니다.",
    "pager.prev": "← 이전",
    "pager.next": "다음 →",
    "pager.page": "{p} / {pages} 페이지",
}

TRANSLATIONS = {"en": EN, "ko": KO}
