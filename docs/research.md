# How it works: data sources & the Times Gate display

This documents the (sometimes undocumented) APIs the widget relies on, so the
code isn't magic. None of it requires an Anthropic API/Admin key.

---

## 1. Claude usage (Pro / Max consumer plans)

Consumer Claude subscriptions have **no API/Admin-key usage report** — the
Anthropic *Usage & Cost API* needs an `sk-ant-admin-…` key that only
**organizations** can create. (Assuming otherwise is the classic dead end.)

Instead, the data shown by Claude Code's `/usage` command comes from an
OAuth-authenticated endpoint we can call with the token Claude Code already
stores locally:

**Token source:** `~/.claude/.credentials.json` → `claudeAiOauth.accessToken`
(an `sk-ant-oat01-…` OAuth token). Claude Code keeps it refreshed; the widget
also refreshes it via the stored `refreshToken` when expired.

**Endpoints**

```
GET https://api.anthropic.com/api/oauth/profile     # plan / org info
GET https://api.anthropic.com/api/oauth/usage       # rolling-window utilization
    Authorization: Bearer <accessToken>
    anthropic-beta: oauth-2025-04-20
```

**`/usage` response (relevant fields)**

```json
{
  "five_hour": { "utilization": 11.0, "resets_at": "2026-06-19T01:10:00Z" },
  "seven_day": { "utilization":  2.0, "resets_at": "2026-06-25T03:00:00Z" },
  "seven_day_opus":   null,
  "seven_day_sonnet": { "utilization": 0.0, "resets_at": null },
  "limits": [ { "kind": "session",    "percent": 11, "resets_at": "..." },
              { "kind": "weekly_all",  "percent": 2,  "resets_at": "..." } ]
}
```

`utilization` is the percent **used** of that window. The widget shows the
5-hour ("SESSION") and 7-day ("WEEKLY") values. `/profile` supplies the plan
label via `organization.rate_limit_tier`
(`default_claude_max_5x` → "MAX 5x", `default_claude_pro` → "PRO", …).

> The cached `subscriptionType` in `.credentials.json` can be stale — trust the
> live `/profile` response.

### Pro vs Max

Identical mechanism. Pro has both 5-hour and weekly limits, so both rows
populate (just with smaller underlying budgets) and the badge reads `PRO`. Free
tier may return `seven_day: null`.

> ⚠️ This is an internal endpoint used by Claude Code, not a published API.
> Anthropic may change it without notice.

---

## 2. GitHub Copilot usage (individual Pro) — optional, not shown by default

GitHub exposes per-user billing usage under the enhanced billing platform:

```
GET https://api.github.com/users/{user}/settings/billing/premium_request/usage   # legacy
GET https://api.github.com/users/{user}/settings/billing/ai_credit/usage          # usage-based
```

These need a token with the user **`Plan`** permission. A **GitHub App
user-to-server OAuth token** (`gho_`/`ghu_`) works; classic and fine-grained PATs
do **not** for the personal billing scope. App user tokens expire (~8h by
default), so they must be re-minted — see
[`scripts/get-github-token.mjs`](../scripts/get-github-token.mjs) (device flow).

The provider (`usage_widget/copilot.py`) parses these responses best-effort and
degrades gracefully on a 401. It isn't drawn by the default layout, but the code
is kept for anyone who wants to wire it back in.

---

## 3. Times Gate display

- **Discovery:** `GET https://app.divoom-gz.com/Device/ReturnSameLANDevice`
  returns the Divoom devices on your public IP with `DevicePrivateIP`,
  `DeviceId`, and `DeviceMac` — handy for finding the device's LAN IP.
- The five LCD panels are **128×128**. Target one with `Draw/SendHttpGif` plus a
  5-element `LcdArray` (one flag per panel) and a base64 **JPEG**
  (`PicWidth: 128`). Optionally `Channel/SetCustom` with `LcdIndex` first.
- **Firmware quirk:** current Times Gate firmware answers local commands with
  `{"error_code": "DeviceToken is err"}` **yet still applies the draw**. The
  client treats that specific code as success and only fails on transport errors.
- `Draw/SendHttpText` / `SendHttpItemList` text layout across panels proved
  unreliable, which is why the widget renders an image and pushes that instead.

---

## References

- ccusage (reads the same local Claude logs for token/cost analytics): <https://github.com/ryoppippi/ccusage>
- Divoom local API docs: <http://doc.divoom-gz.com/>
- pixoo-rest / SomethingWithComputers-pixoo (community Pixoo libraries)
