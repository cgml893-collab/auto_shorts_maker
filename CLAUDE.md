# AutoShortsMaker — 에이전트 워크플로우

대상 저장소: `cgml893-collab/auto_shorts_maker`  
대상 앱: Python 숏폼 파이프라인 (`main.py`), Streamlit 대시보드 (`app.py`), FastAPI 모바일 서버 (`server.py`), 라이선스 (`license_lock.py`).

이 문서는 Cursor / Claude Code가 **오류 재현 → 최신 문서 검색 → 코드 수정 → UI 검증 → GitHub 커밋/푸시**를 한 흐름으로 처리하기 위한 지침이다.

## 1. MCP 도구

프로젝트에 아래 설정이 있다.

| 파일 | 호스트 |
| --- | --- |
| `.cursor/mcp.json` | Cursor |
| `.mcp.json` | Claude Code (프로젝트 스코프) |
| `claude_desktop_config.json` | Claude Desktop에 복사할 템플릿 (`%APPDATA%\Claude\claude_desktop_config.json`) |

등록된 서버:

| 서버 | 용도 |
| --- | --- |
| `github` | `cgml893-collab/auto_shorts_maker` 이슈/PR/커밋/푸시·원격 상태 |
| `brave-search` | fal.ai, ElevenLabs, Streamlit, FastAPI 등 **최신** API/에러 문서 검색 |
| `fetch` | 검색으로 찾은 공식 문서 URL을 마크다운으로 가져와 근거로 사용 |
| `sqlite` | `data/license_lock.db` — `license_lock` / `device_usage` 점검·집계 |
| `puppeteer` | Streamlit / 웹 대시보드 자동 클릭·스크린샷·회귀 확인 |

### 1.1 로컬 시크릿 (커밋 금지)

`.env`에만 넣고, 설정 파일에는 `${VAR}`만 둔다.

```
GITHUB_PERSONAL_ACCESS_TOKEN=  # repo 권한 있는 PAT (Contents: write)
BRAVE_API_KEY=                 # Brave Search API
OPENAI_API_KEY=
ELEVENLABS_API_KEY=
FAL_KEY=
```

GitHub PAT는 fine-grained로 이 저장소만 허용하는 것을 권장한다. 토큰·키·라이선스 실값을 커밋하지 않는다.

Cursor/Claude를 연 뒤 MCP가 빨간색이면: Node(`npx`), 선택적으로 `uv`/`uvx`, 환경 변수, Cursor 재시작을 확인한다. Claude Code는 프로젝트 `.mcp.json` 서버를 한 번 승인해야 한다.

## 2. 원스톱 오류 처리 루프

오류가 보이면 추측으로 패치하지 말고 아래 순서를 따른다.

1. **재현·수집**  
   스택 트레이스, HTTP 상태, 사용 API(fal / ElevenLabs / OpenAI), 관련 파일(`main.py`, `app.py`, `server.py`, `license_lock.py`)을 확보한다.
2. **검색 (`brave-search`)**  
   패키지 버전과 에러 문자열을 넣어 공식 문서를 찾는다. 예:
   - `fal.ai queue subscribe API 2026`
   - `ElevenLabs text to speech API voice_id error`
   - `moviepy 2 concatenate_videoclips AudioFileClip`
3. **원문 확인 (`fetch`)**  
   검색 스니펫만 믿지 말고 공식 URL을 fetch한 뒤, 현재 코드의 호출부(엔드포인트, 헤더, 모델명)와 대조한다.
4. **최소 수정**  
   해당 버그/문서 불일치만 고친다. 관련 없는 리팩터·포맷 대규모 변경은 하지 않는다.
5. **라이선스/사용량 (`sqlite`)**  
   인증, 무료 횟수, 플랜(basic/pro) 관련이면 `data/schema.sql`을 적용한 뒤 `license_lock` / `device_usage`를 조회한다. 런타임 원본은 `%USERPROFILE%\.auto_shorts_maker\`의 JSON이다. DB에 개인 HWID·키 원문을 넣지 말고 fingerprint·집계만 둔다.
6. **UI 검증 (`puppeteer`)**  
   Streamlit/웹을 건드렸으면 로컬 서버를 띄운 뒤 라이선스 게이트 → 메인 폼 → 핵심 버튼까지 클릭하고 스크린샷으로 확인한다.
   - Streamlit: `streamlit run app.py` (기본 `http://localhost:8501`)
   - API: `uvicorn server:app --reload` (기본 `http://127.0.0.1:8000`)
7. **GitHub (`github` + git)**  
   검증 후 커밋하고 `origin`의 현재 브랜치에 푸시한다. 기본 원격 저장소는 `cgml893-collab/auto_shorts_maker`이다.

## 3. Git 규칙

- 커밋 메시지는 왜 고쳤는지 한두 문장. 예: `fix: align fal client call with current queue API`
- `.env`, `*.db`, `output/` 미디어, 라이선스 키, PAT는 커밋하지 않는다.
- `main`/`master`에 강제 푸시하지 않는다.
- 푸시 전 `git status`로 시크릿이 스테이징되지 않았는지 확인한다.
- 사용자가 푸시를 명시한 경우(이 저장소의 운영 루프)에는 수정 완료 후 커밋+푸시까지 수행한다. 그 외에는 커밋 여부를 묻는다.

## 4. 제품 제약

- 영상/음성: fal.ai, ElevenLabs, OpenAI. 키가 없으면 해당 경로를 스킵하거나 명확히 실패시킨다. API 변경은 반드시 최신 문서를 본 뒤에 맞춘다.
- 라이선스: 기기 1대 바인딩, 플랜 `free` / `basic` / `pro`. 마스터 키·서명 pepper·HWID 우회 로직을 약화시키지 않는다.
- UI: Streamlit 라이선스 게이트(`라이선스 인증`)가 깨지면 본편 화면에 진입할 수 없다. 스타일/카피 변경 후에도 이 흐름을 검증한다.
- 모바일: `server.py` + `mobile/` Flutter. API 계약(업로드, 작업 큐, 라이선스 헤더)을 서버만 바꾸고 앱을 안 고치면 안 된다.

## 5. SQLite 점검 쿼리 예

최초 1회:

```sql
.read data/schema.sql
```

```sql
SELECT source, hwid, plan, activated_at FROM license_lock ORDER BY activated_at DESC;
SELECT hwid, plan, free_used, last_used_at FROM device_usage ORDER BY last_used_at DESC;
```

JSON 런타임 파일과 스키마가 어긋나면 코드(`license_lock.py`)를 우선하고, 스키마를 코드에 맞춘다.

## 6. Puppeteer 스모크 (Streamlit)

1. `streamlit run app.py`가 listen 할 때까지 대기한다.
2. `http://localhost:8501`을 연다.
3. 라이선스 화면: 머신 코드/HWID 칩, License Key 입력, `라이선스 인증하고 시작하기` 버튼이 보이는지 확인한다.
4. 인증 이후: 프리셋/업로드/생성 컨트롤이 보이는지 확인한다. (실키 없이 인증을 뚫지 말 것)
5. 콘솔 에러·빈 흰 화면이면 스크린샷을 남기고 2절 루프로 돌아간다.

## 7. 검색 우선 URL

- fal.ai: `https://docs.fal.ai/` , `https://fal.ai/models`
- ElevenLabs: `https://elevenlabs.io/docs`
- Streamlit: `https://docs.streamlit.io/`
- FastAPI: `https://fastapi.tiangolo.com/`
- GitHub MCP: `https://github.com/github/github-mcp-server`

학습 시점의 API 시그니처를 그대로 쓰지 말고, 위 루프로 문서를 갱신한 다음 코드를 고친다.
