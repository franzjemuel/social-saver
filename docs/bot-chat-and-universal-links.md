# Bot chat buttons and universal-link contract

## Product surfaces

**Telegram bot DM:** A user pastes a supported link and receives real, playable,
forwardable media in chat. The bot also handles welcome/help, buttons,
notifications, and delivery actions.

**Telegram Mini App:** The private archive workspace for browsing profiles,
reviewing posts, managing backups, and inspecting job progress. Every API
action authenticates the Telegram Mini App user. A chat URL never contains
internal user IDs, R2 keys, or provider credentials.

## Real bot-chat buttons in PR #37

/start or /menu displays a persistent Telegram reply keyboard under the
message composer with four actions: Open Archive, Add Profile, Save a Link,
and Help. The first two are actual Telegram Web App launch buttons, and the
latter two send an informational request. Users can still paste any link
normally while the keyboard is present.

Commands /app and /add and the /help response include in-message inline
buttons. Read-only inline guidance callbacks are idempotent; they don't queue
jobs or modify data. The global Telegram menu opens the archive workspace.

Verified published destinations as of 2026-10-12:

- Archive: https://social-story-saver.lovable.app/
- Add Profile: https://social-story-saver.lovable.app/profiles/add

The Add Profile page needs Telegram authentication when launched from
inside Telegram. The TELEGRAM_MINI_APP_URL environment setting can replace
the published HTTPS origin for another deployment. Group chat commands
do not attempt to use Web App buttons intended for a private bot DM.

Existing /archive with an Instagram URL retains its archive behavior.
Bare /archive opens the Mini App. /myarchive remains a legacy chat browser.
/status currently shows system-wide health and should not appear as a
customer-facing personal jobs button until safely refactored.

## End-state: universal link intent router

The goal is **one paste box** and no platform selection. First validate
the public host and determine link type, then dispatch to the relevant
provider adapter. The bot ingress never runs provider media downloads or
metered services.

| Pasted URL type | Desired bot behavior | Current implementation |
| --- | --- | --- |
| TikTok public video, direct/short | Native forwardable MP4 | Staging accepted |
| TikTok photos or carousel | Ordered photos / native album | Generic bot delivery not yet shipped |
| TikTok public Story | Download accessible Story media | Not yet shipped |
| TikTok profile | Preview, ask which action to take | Mini App profile path exists; chat URL handling pending |
| Instagram Reel/feed | Media into chat | Existing media resolver; live reliability depends on provider |
| Instagram Story link or username | Story media when available | Separate /stories command; URL routing pending |
| Instagram profile | Preview, profile image or stories by choice | Generic link handling pending |
| Private/unavailable link | Honest error and recovery option | Provider-specific |

A profile URL must not silently launch full account backup. Instead offer
Preview/Browse, Profile Picture when supported, Current Stories when
supported, and Back Up after explicit scope and entitlement confirmation.
Keep Story/Highlight acquisition behind separate provider and spend gates;
do not use paid Apify without explicit user approval.

## Safe engineering sequence

1. Bot chat buttons (this PR): actual reply and inline keyboards, navigation
   to two verified routes, no changes to TikTok Quick Save pipeline.
2. Personal job status: tenant-scoped summaries and admin-only system health.
3. Capability-based link router: classify TikTok/Instagram URL intents safely,
   with explicit unsupported states and no accidental large imports.
4. Feature-specific provider support: TikTok photo/carousel, Stories and
   Highlights, Instagram profile/Story intents, with isolated release gates.
5. Reliable terminal notifications: durable, idempotent outbox, separate from
   irreversible media delivery and job finalization.

Staging deploys automatically when reviewed code merges into main.
PR #37 is review-only until the user explicitly approves the merge.
