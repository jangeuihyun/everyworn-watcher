# everyworn 재고 알리미

everyworn의 **Flannel Relaxed Curve Shirt (Green)** 사이즈별 재고를 10분마다 확인해서,
바뀌면 텔레그램으로 알려줍니다. GitHub Actions에서 무료로 돌아가요.

- 알림 예시: "🟢 Flannel Relaxed Curve Shirt (Green) — 0(90) 재입고!"
- 현재 재고표는 저장소의 `STATUS.md`에서 언제든 확인 가능

## 구성

| 파일 | 역할 |
|---|---|
| `watcher.py` | 쇼핑몰에서 사이즈별 재고를 읽고, 바뀌면 텔레그램으로 보냄 |
| `.github/workflows/watch.yml` | GitHub이 10분마다 `watcher.py`를 실행하도록 하는 설정 |
| `state.json`, `STATUS.md` | 실행하면서 자동으로 생기는 직전 재고 기록과 재고표 |

텔레그램 봇 토큰은 저장소 **Settings → Secrets and variables → Actions**에
`TELEGRAM_BOT_TOKEN`으로 저장돼 있어요 (공개되지 않음).
알림 받을 대화방은 봇에게 보낸 메시지로 자동으로 찾아 기억해요.

## 감시 상품 바꾸기

`watcher.py` 위쪽의 `EXTRA_IDX`에 상품 번호를 넣으면 돼요.
상품 번호는 상품 주소 `https://www.everyworn.com/shop/?idx=288`의 마지막 숫자예요.

```python
EXTRA_IDX = [288]        # Flannel Green만
EXTRA_IDX = [288, 285]   # Flannel Green + Red
```

## 멈추기 / 다시 켜기

**Actions** 탭 → 왼쪽 "everyworn 재고 감시" → 오른쪽 위 **⋯** → **Disable workflow**.
다시 켤 땐 같은 화면의 **Enable workflow**.

## 옵션

- **재입고만 알림 받기**: Settings → Secrets and variables → Actions → **Variables** 탭에서
  `NOTIFY_SOLDOUT` = `false` 추가 (기본은 품절 전환도 알림).
- **확인 주기 바꾸기**: `.github/workflows/watch.yml`의 `*/10`을 원하는 분 단위로 (최소 5).

## 알아두면 좋은 점

- GitHub의 예약 실행은 붐비는 시간대에 몇 분~수십 분 늦게 돌기도 해요.
- 저장소에 60일간 아무 활동이 없으면 GitHub이 예약 실행을 자동으로 멈춰요. Actions 탭에서 다시 켜면 돼요.
- 사이트 구조가 바뀌어서 조회가 1시간 연속(6회) 실패하면 텔레그램으로 경고가 와요.
