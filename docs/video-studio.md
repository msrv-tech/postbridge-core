# Video studio

Video studio creates downloadable vertical MP4 drafts from managed screenshots, photos and clips. Optional autopilot writes a storyboard through the existing AI gateway, reuses supplied assets and generates missing illustrations. Existing agent schedules can prepare recurring video drafts. A completed revision can become an editable Postbridge post draft, with a separate exact-content approval for Telegram video delivery. Generation and delivery flags are disabled by default.

The shared UI is at `/workspaces/{workspaceId}/content/videos`. Hosted requests go through the SaaS BFF; self-host requests use authenticated `/api/app/videos` endpoints. Core owns projects, immutable input revisions, checkpointed jobs and output assets. SaaS owns roles and budget reservations. Its existing SDK assistant reads and edits the selected storyboard, starts funded production, creates voice/language variants and prepares persisted schedule/post cards. See the storyboard section below.

## Enable in a test installation

1. Apply Core migrations with `alembic upgrade head`, including `20261019_video_studio`, `20261019_video_autopilot`, `20261019_video_agent_schedules`, `20261019_video_post_drafts`, `20261019_video_approvals`, `20261019_video_delivery_time`, `20261019_video_speech` and `20261020_video_materials`. Hosted installations must also apply SaaS `20261019_video_budget`, `20261019_video_schedule_budget` and `20261019_video_publication`.
2. Set `VIDEO_STUDIO_ENABLED=1` for Core API and workers. Configure the existing `MEDIA_STORAGE_TYPE`, storage path or S3 settings, and an externally reachable `MEDIA_BASE_URL`. API and all workers must use the same bucket or a shared local volume; separate container filesystems do not share local media.
3. Run the regular worker and one beat scheduler. Core dispatches durable video jobs every 30 seconds. Broker failures leave jobs in the database for a later attempt.
4. Start a dedicated worker with `celery -A postbridge.workers.celery_app:celery_app worker -Q video --concurrency=1 --prefetch-multiplier=1 -l info`.
5. For autopilot, configure the settings below and validate a real storyboard and vertical image against the funded gateway before enabling a paid pilot.

The root Compose `video` profile provides a dedicated worker and beat for local development. Self-host and hosted production profiles provide a video worker and reuse their regular worker's beat. Hosted development provides `core-video-worker` in the `full` profile. Do not start a second beat scheduler. Core and CI images include FFmpeg, FFprobe and DejaVu fonts. Compose video workers have CPU, memory and process limits; delivery workers do not consume the `video` queue.

| Setting | Scope and meaning |
| --- | --- |
| `VIDEO_AI_ENABLED=1` | Core API and video worker; enables paid storyboard and image generation when dependencies are configured |
| `AI_GATEWAY_BASE_URL`, `AI_GATEWAY_API_KEY` | Existing funded gateway connection; users do not supply separate provider credentials |
| `AI_GATEWAY_DEFAULT_MODEL` | Multimodal model supporting image input and structured JSON for analysis/storyboards |
| `AI_IMAGE_GENERATION_MODEL` | Image model supporting inline `b64_json` responses through `/v1/images/generations` |
| `VIDEO_SPEECH_ENABLED=1` | Core API and video worker; opt-in Google narration and language/voice variants, disabled by default |
| `VIDEO_SPEECH_MODEL` | Currently only `gemini-2.5-flash-tts`; the funded gateway must expose the JSON `/v1/audio/generations` contract |
| `POSTBRIDGE_APP_MODE=saas` | Hosted Core API and workers; every paid step requires a SaaS reservation grant |
| `SAAS_BASE_URL`, `STATUS_EVENT_WEBHOOK_TOKEN` | Hosted Core callback destination and existing event token; must match SaaS `CORE_EVENT_TOKEN` |
| `VIDEO_AUTOPILOT_RESERVE_UNITS` | SaaS setting; positive conservative reservation per run in GitSell units. Default `0` keeps hosted autopilot unavailable |
| `VIDEO_AI_SELFHOST_BUDGET_UNITS` | Core self-host setting; positive per-job stopping threshold, default `0`. No SaaS subscription reservation |

Hosted settlement also needs the regular SaaS worker/beat with working `CORE_BASE_URL` and `CORE_SERVICE_TOKEN`. Natural-language edits use the existing SaaS assistant configuration (`AI_ASSISTANT_ENABLED` and its funded gateway); there is no second assistant service.

## Supported operation

- Up to eight PNG, JPEG, WebP, MP4 or WebM materials, 64 MiB each, up to 180 seconds per clip and 16 megapixels per frame. Uploads are spooled and transferred through files.
- Up to 45 seconds per output; each scene lasts 1–15 seconds. Output is 1080×1920, 24 fps, H.264/AAC with fast-start metadata. This is the internal montage profile; platform acceptance still needs live validation.
- Autopilot accepts a task with optional files. It plans up to six scenes, reuses uploaded assets preferentially and generates missing illustrations. Inputs and storage are checked before the first paid call. Without configured autopilot, a task without files becomes an ordinary draft.
- The multimodal gateway analyzes a screenshot or up to five sampled clip frames and records visible facts and timestamps. The storyboard can choose a bounded clip fragment; image scenes support zoom/pan and fades. Opt-in Veo video scenes and mixed montage are described below. Optional Google narration is described below.
- Scene order, duration and on-screen text can be edited manually. Source proportions are retained. Clip audio is normalized to stereo 48 kHz; image scenes receive silence. Short clips loop to the chosen duration.
- The hosted assistant can create a project, edit/reorder existing scenes, replace/generate a selected illustration, or create a voice/language variant. Server code fixes ownership, role, selected revision, budget and request key. Unsaved edits block mutations. Schedule and post preparation use explicit action cards; destination selection and publication approval stay in the post editor.
- Each request has a tenant-scoped unique key. Replaying unchanged inputs creates no new job or reserve. Responses include `requested_revision` even when a newer revision is current. Expected versions prevent stale writes. At most two queued/running jobs are admitted per tenant.
- A worker claims a job under a lease. Duplicate deliveries do not run concurrently. Expired jobs can be recovered, with at most three claims. Paid results are checkpointed and reused. Cancellation stops later steps; a completed provider call still contributes its actual charge.
- Inputs, generated illustrations and outputs referenced by projects are protected from ordinary media deletion. Projects currently retain their revisions and source assets.

The API accepts managed asset IDs, not arbitrary source URLs. Core checks ownership; hosted mutations use existing orchestration roles. FFmpeg uses argument arrays, restricted input formats/protocols and time limits. Captions use text files with expression expansion disabled.

## Google narration and language versions

The first integration supports Russian/English and the prebuilt Google voices Kore, Charon and Puck with neutral, warm or energetic delivery. With the speech flag enabled, new autopilot videos request concise spoken text in addition to on-screen headings. The existing funded gateway calls Gemini 2.5 Flash TTS through its existing Vertex identity; Postbridge needs no new Google credentials. Deploy the gateway speech endpoint, configure its model billing and enable `AI_SPEECH_GENERATION_ENABLED` there before enabling Core's speech flag. A live provider test and measured reserve sizing are still required.

**Add language** or **Change narration** uses `POST /{projectId}/voice-variants` with `expected_version`, `source_version`, `request_key`, `language`, `voice` and `voice_style`. The server selects only a completed immutable source revision in the same project. It creates another revision in that project with source provenance and its own output/job/usage; it does not regenerate images or clips. A different language runs a checkpointed text adaptation before speech, while a voice-only change reuses the original text. Prior files and prepared posts are never overwritten. The language/version selector can preview and prepare a separate post from each result. Every post uses the existing independent exact-file publication approval. Post preparation accepts optional `source_version` alongside the current project's optimistic `expected_version`.

Speech is saved per scene as verified 24 kHz mono 16-bit WAV, with a tenant-scoped cache keyed by text, language, model, voice and delivery style. Native provider token counts are converted only by GitSell's existing billing service; Core records the gateway's explicit normalized charge and external request ID. Missing usage or a lost provider reply never becomes an estimated customer bill or a blind paid retry. Existing budget reservations and background settlement apply to voiced manual revisions and language variants as well as autopilot. Cache hits do not call or charge the provider again, though hosted admission conservatively still requires a reserve.

The scene duration expands to the measured audio duration, rounded up to a video frame. Audio beyond 15 seconds per scene or 45 seconds total is preserved where valid and stops assembly with a request to shorten the script, rather than clipping speech or making unlimited paid retries. Phrase-level burned-in subtitles show the actual spoken text over its measured audio interval; word-level subtitles now use local acoustic alignment against the approved transcript. Unreliable or unavailable alignment keeps the measured phrase subtitles; word times are never guessed. Source clip audio is replaced in narrated scenes. Changing an on-screen heading preserves speech; changing spoken text regenerates only missing speech blocks while retaining other cached audio. Manual edits and assistant caption/duration edits preserve audio IDs and language, and Core rejects transcripts or shortened durations that do not match their verified audio.

The assistant receives the selected server-verified revision, with the latest project version retained for locking. Existing caption/duration tools therefore edit the selected language, not whichever variant was most recently created. Assistant tools can now create a language/voice variant through the same funded production service. Schedule templates can retain the selected version's verified speech as manual assembly or create fresh narrated autopilot drafts. Scheduled batches support multiple language/voice versions in one occurrence. Uploaded personal narration, voice cloning, additional languages, automatic screenshot translation and guaranteed provider-side hard spending ceilings are outside this increment.

Gateway contract: `/v1/audio/generations` accepts `{model, text, language, voice, style}` and returns `{id, model, audio: {b64_pcm, sample_rate: 24000, channels: 1, sample_width: 2}, provider_usage, usage_tokens_charged}`. This is a JSON GitSell contract, not the OpenAI binary speech API. Google setup and transport follow the [official Gemini-TTS Vertex guide](https://docs.cloud.google.com/text-to-speech/docs/gemini-tts). The speech cache migration refuses downgrade while it contains speech assets, because older workers cannot preserve their narration fields.

## Paid operations and recovery

SaaS locks the workspace and reserves units from its existing AI content pool before creating an autopilot revision. Pending reservations also count against subsequent content/adaptation budget checks. Core reauthorizes before each external call. SaaS periodically reconciles reported usage and releases unused holds after terminal jobs, independently of browser polling or subscription expiry.

This gateway reports normalized GitSell charges in `usage.total_tokens` (or an explicit charged-units field). These are neither dollars nor raw model token counts. Never guess missing usage. Reservation sizing must be measured for the selected models. The stopping threshold is checked between calls; the gateway currently cannot enforce a per-request credit ceiling, so one paid call can exceed the reserved estimate. Its actual charge is recorded and later paid steps stop. This is not a guaranteed hard provider-spend cap.

Paid steps persist `authorizing → started → received → done`. A timeout, missing usage or worker loss with a started request requires reconciliation; no paid retry is issued. Known usage is charged incrementally and idempotently. Unknown amounts keep the remaining hold. A platform admin may resolve them after checking the gateway ledger through `POST /workspaces/{workspaceId}/videos/budget/{reservationId}/resolve` with `actual_units` and an audited reason. This endpoint does not restart generation. A never-authorized hold with no Core request may expire after one hour; a late worker cannot authorize it.

Only known totals are shown in the studio. Uncertain results show a reconciliation message. After manual settlement, the hosted BFF and assistant display the confirmed SaaS total while Core retains its original provider checkpoints. Operator ledger reconciliation and live gateway validation are required before general release.

## Scheduled drafts

`/workspaces/{workspaceId}/agents/videos` manages video tasks through the existing agent task/run API. A completed project can seed a new schedule with its current managed materials. Users choose daily, weekday or weekly generation, local time and an IANA timezone. The default timezone comes from their profile, then the browser. The existing scheduler calculates UTC instants in that timezone; valid timezones now also apply to existing text-agent tasks. There is no separate video clock or beat service.

Video tasks use `mode=video_autopilot`, `autonomy_mode=draft_approval` and one video per run. `task_config` contains a stable creation `request_key`, a `video` template, optional `max_units_per_run` / `max_units_per_month`, and `notify`. The template can request autopilot or assemble only uploaded media. Hosted actors come from the authenticated user, never a client-selected billing identity. Direct Core callers supply their existing service/self-host actor identity.

Each due occurrence first creates an ordinary agent run plus a `video_agent_executions` intent with an immutable template, selected asset IDs, actor and budget policy. A unique occurrence key prevents duplicate work. `Idempotency-Key` similarly protects manual run requests. If the previous occurrence is still queued/running, the new occurrence is recorded as skipped. Missed times advance from the current time rather than creating a catch-up burst. Admission moves the next schedule time before any heavy work or broker call.

The existing video dispatcher recovers intents and sends preparation to the `video` queue. Preparation reserves the hosted budget through an authenticated callback, then enters the same production service used by the manual studio. A committed project can be reattached after a lost acknowledgement. Existing paid production checkpoints remain authoritative; generic agent usage events do not charge a second time. Workspace quota checks include reservations. Schedule monthly limits include actual usage and pending holds in the UTC admission/reservation month. Self-host checks its own per-job threshold and optional template monthly limit. As with manual production, a single provider call may exceed an estimate before the next step stops.

Pausing or archiving prevents future scheduled occurrences. It does not cancel an admitted run; cancellation remains in video studio. Hosted preparation and each paid step recheck the template owner's current management rights. A revoked owner stops new paid work; failed preparation for that reason pauses the schedule. Already incurred workspace charges still settle after membership removal. Templates and admitted runs protect their source materials from ordinary deletion. Generic agent cleanup excludes video provenance until a dedicated retention policy exists.

When production finishes, the original agent run becomes `awaiting_review`, `failed` or `cancelled` and links to the exact video revision. Video projects link back to the task and run. Hosted completion callbacks enqueue one notification in the existing outbox when enabled, using the actor's configured contact; actual email/Telegram delivery depends on existing notification settings. Self-host currently provides the result/history without notification delivery. Archiving does not erase prior run results.

This first schedule UI supports creation, run now, pause, resume and archive. To change a template, create a replacement schedule and archive the old one. Templates can be edited in place by their creator using an optimistic snapshot hash; admitted occurrences keep their original input snapshot. Dynamic folder inputs resolve the newest 1–8 tenant-owned materials at admission; an empty folder fails before reserving a budget. Multiple originals and voice variants are supported. Automatic delivery-time selection from a production template remains future work. The produced file is a draft; `awaiting_review` is not a publication approval. A user can separately review a completed video and schedule its delivery below.

## Prepare an editable post

For a successfully assembled MP4, **Prepare post** reveals an optional post-text field. The production brief is never copied into publishable text. Unsaved scene changes block preparation until a new video is assembled. The server checks tenant ownership, the expected current version, completed job and managed MP4, then atomically creates an ordinary `source_type=postbridge` content item in `draft` status and a `video_post_drafts` receipt. It makes no provider call, publication plan, target or delivery job.

One revision has at most one prepared post. Replaying the same version and caption returns that post even after a newer video revision exists. A different caption on replay is rejected; further post edits belong in the post editor. The post retains its original MP4 when the video is revised. Deleting the post leaves the receipt with a null content reference, so a delayed retry cannot recreate it. A new video revision can prepare its own post. Project history links each version to its existing post, including when generation is disabled.

The post editor shows a video preview and a link to its source version. Hosted preparation checks the same connection-management role as ordinary post creation and writes an audit event. It does not consume AI units. The endpoint accepts only the expected version and caption; clients cannot inject an asset, destination, publication status or time. Video posts use the separate delivery card below; ordinary live-sync controls are hidden.

Preparing a post creates no publication approval. The canonical post remains editable until the delivery card is confirmed.

## Approve and deliver a video

`VIDEO_PUBLICATION_ENABLED=1` on Core API and regular delivery workers enables the first delivery pilot. This flag is independent of generation. The existing regular beat dispatches pending approved targets every 30 seconds; it needs the regular delivery worker, not the video-render worker. No real account access or publication is performed by automated tests.

**Save and review delivery** saves a draft and loads the server snapshot: the exact MP4 version, text and available destinations. The outgoing text joins the title, Markdown body, summary, CTA and link as displayed paragraphs, with no Markdown interpretation, hidden link-back, truncation or AI adaptation. Changing the form blocks confirmation until saved and reviewed again. The assistant starts collapsed on video post editors.

**Confirm and send** locks the content row and checks its fingerprint, original completed MP4, media size, HTTPS URL, destination tenant/status and stored bot credentials. This pilot supports selected connected Telegram channels and `sendVideo` only. It conservatively limits URL delivery to 20 MiB and captions to 1024 UTF-16 code units. See the [Telegram sendVideo contract](https://core.telegram.org/bots/api#sendvideo) and [URL upload limits](https://core.telegram.org/bots/api#sending-files). The media host must be publicly reachable; local checks do not verify DNS reachability or live bot administrator permissions.

Core commits one immutable approval, one existing publication plan and one target per selected channel in the same transaction. Repeated confirmation returns the same plan. A different snapshot or set of destinations is rejected. Once approved, editing and deletion are blocked; changes require a post prepared from a new video revision. Copying a managed output URL into a generic publication, live-sync delivery, a legacy schedule or an ordinary post publication cannot bypass approval. The shared executor rechecks the approved source and destination immediately before sending and uses the approval text/media instead of mutable render variants.

The plan is the durable outbox: broker failure leaves pending targets for later dispatch, and duplicate worker messages share the normal atomic claim. Each destination shows its own status and external ID, plus a link for public Telegram usernames. A successful destination is never repeated. A confirmed Telegram rejection can be explicitly retried after correcting the connection. Transport errors, 5xx responses, missing message IDs and expired publishing claims become `unknown` with no automatic or UI resend. Operators must verify the external result before a future reconciliation workflow; that workflow is not implemented yet. Cancel affects only targets still pending; already claimed requests may finish.

Hosted approval reserves one existing publication-quota slot before calling Core. Admission is keyed by workspace and content ID. Unknown RPC outcomes retain that slot and the original Core body, so retries cannot consume a second slot or change destinations. A definitive 4xx rejection releases the slot in its original billing month. Confirmed approvals count once, regardless of target count or later cancellation. No new AI charges are added. Approval, retry and cancellation are audited through existing SaaS roles.

YouTube publishing, validated Instagram Reels/Shorts, bridge selection with platform adaptations and operator reconciliation of unknown sends remain future work. This pilot uses explicit destination channels rather than live-sync bridge fan-out. Production PostgreSQL concurrency and a real-account pilot must pass before enabling it broadly.

## Delivery time and missed windows

The confirmation card defaults to immediate delivery. **Send later** reveals a local date/time and IANA timezone, initially using the user's profile or browser zone. The preview states the chosen wall time and zone. The browser refuses nonexistent or repeated times during clock changes instead of guessing an offset. Core and SaaS require an explicitly timezone-aware instant and valid IANA zone, normalize to UTC, and require whole minutes. A new scheduled time must be at least one minute ahead and at most 365 days away.

Delivery time is independent of the agent's production schedule. It belongs to the existing publication plan and targets; no Celery ETA or browser timer authorizes a send. The dispatcher selects only due plans, and the atomic target claim locks the canonical content row and checks the currently approved window. An early manual dispatch or a stale broker message cannot start delivery early. Claims, rescheduling and cancellation share the same content lock.

New approvals have a 15-minute start window after the selected time, or after confirmation for immediate delivery. If a pending target misses it, the dispatcher or executor marks it `expired` with `VIDEO_DELIVERY_MISSED`, retaining the draft and approval. Expiration runs even while the publication flag is off. A provider request that already started may finish after the window; published, publishing and unknown results are not reset by expiry. Missed windows appear in the card and existing status outbox. A separate user notification for missed windows is not implemented yet.

**Change delivery time** explicitly confirms a new immediate or scheduled window for unsent destinations. It keeps the same content approval, publication plan and quota admission, and never repeats published destinations. Pending, expired, cancelled and confirmed failed destinations can move after source and destination checks. Publishing or unknown results block rescheduling until verified. The server uses an expected schedule version; a retry of the same last change returns its existing result after a lost response. A different stale change is rejected. Up to 100 schedule revisions are retained in history. The original approval request remains replayable even after later schedule changes.

Existing approvals from before this migration retain their original behavior and have no retroactive cutoff; explicitly rescheduling them installs a new window. Downgrading requires cancelling pending deliveries with a deadline first, so an older worker cannot lose their time guard. This feature uses the existing `VIDEO_PUBLICATION_ENABLED` flag and requires no new hosted billing migration. One quota slot stays attributed to the original approval month, including when delivery is moved to another month.

## API and verification

Hosted endpoints use `/workspaces/{workspaceId}/videos`; self-host uses `/api/app/videos`; service callers use `/internal/service/videos` with the existing service token and tenant header. Routes provide capabilities, list/create, material upload, retrieval, revision, cancellation and `POST /{projectId}/post-draft`. Core additionally exposes request-key lookup for background billing. Schedules use existing `/agent/tasks` and `/agent/runs` routes. Hosted internal callbacks additionally use `/internal/video/reserve-agent-run` and `/internal/video/agent-run-finished` with the nonempty shared Core event token.

Delivery endpoints below those video prefixes are `GET /posts/{contentId}/publication`, `POST /posts/{contentId}/publication/approve`, `POST /posts/{contentId}/publication/targets/{targetId}/retry`, `POST /posts/{contentId}/publication/cancel` and `POST /posts/{contentId}/publication/reschedule`. Approval optionally accepts `publish_at` and `timezone`; rescheduling requires `expected_schedule_version` and optionally a new time/zone, without accepting new text, media or destinations.

Canonical Core verification:

```sh
docker compose --progress=plain -f ci/docker-compose.yml build test
docker compose -f ci/docker-compose.yml run --rm test pytest -q tests/test_video_studio.py tests/test_video_autopilot.py tests/test_video_speech.py tests/test_video_schedules.py tests/test_video_post_drafts.py tests/test_video_publication.py tests/test_video_delivery_time.py tests/test_publication_target_executor.py tests/test_media_endpoint.py tests/test_media_proxy.py tests/test_media_storage_and_failure_class.py
```

Tests cover isolation, replay, version conflicts, leases, cancellation, retention, paid checkpoints, callback authorization, lost responses, post-draft deletion and actual FFmpeg MP4 assembly. SaaS tests cover reservations, independent settlement, roles and the typed assistant tool. Browser tests use mocked BFF responses to check creation, simple/mobile layouts, assistant edits and post-editor handoff after a lost acknowledgement. AI gateway responses are mocked; these checks do not establish live provider access, publishing or production PostgreSQL readiness.

## Local hosted acceptance — 2026-10-19

The hosted dev stack was built and started on localhost with PostgreSQL 15, Redis,
Core/SaaS APIs, their regular workers/beat, a dedicated video worker and the shared
Vite frontend. All migrations applied. The optional SaaS
`environments/dev/docker-compose.video.yml` overlay supplies a shared media volume
and private configuration to every relevant service. Runtime secrets remain outside
the repositories. The funded GitSell gateway, including the deployed Google TTS
endpoint, was used for these separate live checks:

| Scenario | Result | Video charge in GitSell units |
| --- | --- | ---: |
| Montage from two uploaded images; repeated creation request | One project and a valid MP4 | 0 |
| Russian narration | Two Google speech blocks and phrase subtitles | 2579 |
| English version of the same project | Translated captions/speech; original visual assets retained | 3516 |
| English voice change to Charon | New speech; original visual assets retained | 2495 |
| Autopilot with supplied images | Storyboard, reused materials, narration and MP4 | 4815 |
| Autopilot without uploads | Two generated images, narration and MP4 | 132384 |
| Assistant caption edit of the selected Russian revision | New revision with the same verified speech assets | 0 additional video generation |
| Natural scheduled occurrence in Asia/Novosibirsk | New voiced MP4, original run `awaiting_review` | 0 |

All sample outputs were probed as 720×1280 H.264/AAC, about ten seconds. Frames were
inspected for Russian headings/subtitles; desktop and 390-pixel mobile UI were checked.
The five paid reservations settled to exactly 145789 units in the workspace counter,
with remaining holds released. Preparing a post from the earlier Russian revision
was idempotent and retained that revision's MP4. The test schedule was paused after
its successful occurrence. Measurements describe these samples, not a fixed price
per video or a provider hard spending cap.

The video post editor hides image-only media URL, upload and generation controls;
its MP4 is managed through the source video revision. The hosted UI build and four
affected editor/browser regressions passed after this correction.

Automated verification: the initial full Core Docker run passed 656 cases and found
one outdated worker-test fixture missing `mode`; after correcting it, all nine
worker tests passed in a rebuilt image and OpenAPI export succeeded. The full SaaS
Docker run passed 860 tests (one skipped), followed by 64 passing video/assistant
regressions in a rebuilt image. Core/SaaS coverage gates passed at 77.98%/78.32%.
All 18 hosted browser tests, three delivery-time tests and the i18n boundary check
passed. Live assistant checks exposed and fixed the missing `/v1` SDK fallback and
read-before-write ordering: context tools must complete before a scene mutation,
and a lost final model reply cannot hide an already saved revision.

This acceptance is local. Hosted production deployment, real external delivery,
platform acceptance and a broader pilot remain release work. The local media URLs
do not satisfy the delivery pilot's public HTTPS requirement.


## Storyboard, media library and assistant tools — 2026-10-19

The canonical storyboard is the immutable revision's effective `scenes`, exposed
as `current.storyboard` with `schema_version=1`. Every produced scene has a stable
`scene_id`, an owned source asset or image prompt, purpose, caption, optional
narration, duration, optional source clip start, motion and transition. Templates
cover problem/solution, product demo and quick tutorial. Optional brand name and
color decorate CTA scenes. UI controls remain inside disclosures.

Autopilot samples one image or five timestamped JPEG frames per clip, bounded to
640 pixels per dimension. A funded multimodal request records observed content,
visible text and a suggested clip start. This is sampled-frame understanding,
not continuous action analysis or source-audio transcription. Analyses are cached
with the owned material and model. The script receives those observations and
product facts from the brief; it must not invent product capabilities. Structured
JSON schemas constrain analysis and storyboard responses. Paid text responses are
checkpointed before parsing so a recovered worker can process them without a
second paid call. Unknown provider outcomes still require reconciliation.

Scene settings support a bounded clip start and duration, image zoom/pan, cuts
or a short fade through black, and CTA brand styling. A sampled frame anchors a
clip window; the planner moves it back when needed to fit its full duration.
Longer translated narration can similarly move the selected clip window back,
without cutting speech; narration longer than the entire selected clip is
rejected rather than silently changing its source. A replacement image is an
explicit scene operation; it uses the normal reservation and image checkpoints.
Other images, clips and verified speech are reused. FFmpeg renders the complete
MP4; unchanged encoded segments are not cached. Each finished scene has a stored
360×640 preview taken from the assembled MP4, with its timeline timestamp.
Previews and source references participate in existing media retention checks.

`GET /videos/materials` browses only owned library entries, with search, folder
filtering and pagination. `PATCH /videos/materials/{id}` changes a name/folder.
Uploads keep their original name. The migration includes existing image/video
sources, excluding produced MP4 outputs; legacy metadata is completed when the
material is analyzed. Material folders can be browsed in creation, scene
replacement and schedules; dynamically choosing files from a folder on every
scheduled occurrence is not implemented.

The existing Agents SDK assistant exposes selected-storyboard reading, library
browsing, project creation, scene changes/replacement/regeneration, language and
voice variants, schedule listing, schedule/control proposals and post-draft
proposals. It uses the same admission/budget services as the UI, a fresh role
check, optimistic project version and one video action per message. The client
reuses a request key after a network failure; the server returns the stored reply
without repeating model tools. An accepted request without a stored reply is
reported as already accepted instead of rerunning a potentially paid action.
Video requests receive server-read workspace/navigation context before model
execution, so mandatory reads do not depend on the model batching its tools.
Read tools can run independently; write tools still use that prepared context
and fresh server admission. Queued production returns a server receipt and
never claims that an image, audio track or MP4 has already finished.

Schedule and post proposals are persisted in the original assistant message,
scoped to its workspace and author. Applying a card rechecks current permissions
and runs the saved payload; a repeated apply returns its saved result. Schedules
remain draft-only. Preparing a post opens the editor for separate destination
selection and exact-file approval; assistant tools never publish externally.


### Acceptance of storyboard and assistant actions

Validated in the local hosted PostgreSQL/Docker environment on 2026-10-19:

| Scenario | Result |
| --- | --- |
| Sampled visual analysis | A synthetic 12-second clip contained `Loading...`, then `Postbridge / Video studio`. The multimodal model recognized both, and the storyboard used three windows after the loading screen. |
| Paid text recovery | After correcting clip-window handling, an operator requeued the saved local response. The job completed with the same 6,162 GT; no provider text request was repeated. The earlier pre-schema failed job's known 17,920 GT remained accounted for. |
| Creation from assistant | One request produced a new 10-second English video without user files: three AI illustrations, captions and per-scene previews, 200,181 GT. |
| One-scene regeneration | The dedicated assistant tool replaced scene 1's video source with an AI illustration, retained its stable scene ID/text/duration, removed clip trimming and preserved the other two source windows, 64,622 GT. Replaying the assistant HTTP request returned the same revision. |
| Language/voice from assistant | A Russian Google/Kore variant retained every visual asset. Its measured speech expanded the output to 11.813 seconds and moved the final clip window back. Three verified 24 kHz speech tracks contained non-silent audio; 6,581 GT. |
| Post card | Preparing a card did not create a post. Applying it created one draft linked to source version 1; replay returned the same post, and later image/voice revisions did not replace its MP4. |
| Schedule cards | Prepared/applied Monday 10:00 Asia/Novosibirsk with 300,000 GT/run and 1,000,000 GT/month, then paused it. The assistant listed it and prepared/applied an archive card. Apply replays did not repeat the operations. |
| Accounting | All ten local acceptance reservations, including the prior smoke scenarios, settled at 441,255 GT, exactly matching the workspace counter. No budget holds remained. |

Final targeted Docker suites passed 96 Core tests and 61 SaaS tests. All 20 hosted
browser scenarios passed, including the library search Enter key not submitting
production, one-scene regeneration controls and explicit schedule-card application.
The hosted UI production build, i18n boundary check, both OpenAPI exports and both
repository whitespace checks passed. Runtime media and sanitized reports are in
`~/.local/share/postbridge/video-storyboard-artifacts/`.

These checks cover the requested local storyboard/editor/assistant increment.
Production Core/SaaS rollout, real external-channel delivery, continuous video
understanding, source-audio transcription, encoded-segment caching and generated
video scenes remain separate work.

## Approved YouTube and Instagram delivery

The exact-content approval route supports Telegram video, Instagram Reel and YouTube Short destinations. YouTube requires an explicit title, privacy choice (default private), audience declaration and synthetic-media disclosure. These settings are stored with the immutable destination snapshot. YouTube is deliberately unavailable through the generic bridge publisher until its video approval contract is satisfied.

The executor persists encrypted provider checkpoints in `video_delivery_states`. Instagram creates a REELS container, polls its status, publishes once and obtains an authoritative permalink. YouTube streams the owned managed MP4 through a saved resumable upload session, probes incomplete/lost uploads, and verifies processing and actual visibility. `processing` deliveries poll the same job after the original start deadline; pending deliveries still obey the 15-minute start window.

Ambiguous responses never create a replacement upload. The reconcile action resumes only a saved provider session. An unknown Instagram publish response without a returned media ID remains unknown and requires external inspection. A confirmed media ID is preserved even if permalink retrieval fails. YouTube visibility mismatches retain the uploaded ID/link and require review rather than uploading again.

Hosted connection management uses Google OAuth with one-use workspace/user state and PKCE. Configuration and provider authorization live in the hosted layer. Real-account permissions, Shorts classification, publication visibility and statistics still require an external pilot; mocked contract checks are not proof of live publishing.

Validation for this increment: 70 focused Core checks, 65 hosted API/OAuth/quota/capability checks, and 24 hosted browser scenarios passed. The real managed-MP4 test simulates a lost upload reply, explicitly reconciles the saved session, and asserts one insert and one byte upload. It does not contact a real social account. Local dev applied `20261021_video_delivery` and `20261021_youtube_connections`; API health and the existing three-version video project were checked. Google/Meta publishing applications are not configured in this dev environment, so external publishing is not accepted yet.

Provider contracts: [YouTube video insertion](https://developers.google.com/youtube/v3/docs/videos/insert), [YouTube resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol), and [Meta's Instagram publishing collection](https://www.postman.com/meta/workspace/instagram/documentation/23987686-9386f468-7714-490f-9bfc-9442db5c8f00). Credentials travel in authorization headers for the new publishers; sensitive upload-session query strings are redacted from HTTPX logs.

A final 32-provider-check run also passed after moving new Reels requests to header authorization and redacting resumable session URLs from HTTPX logs. OpenAPI exports and the web build/i18n check passed.


## Bridge receiving-side configuration

The hosted bridge wizard connects a Postbridge source to a YouTube destination with `settings_json.video_delivery` (Short format, privacy, audience, synthetic-media declaration). Instagram/Telegram may opt into this reviewed-video-only mode; existing ordinary Instagram bridges keep their behavior. These settings are editable on the bridge. No schema migration is needed.

The publication view exposes eligible bridges with resolved destination options and settings hashes. New clients approve `bridge_ids` plus `expected_bridges`; they cannot override per-channel options in this request. Core resolves owned tenant endpoints, captures the bridge identity/settings hash with the exact content approval, and checks it again before delivery. Paused/deleted/changed bridges stop network publication. Fresh reads also protect against stale ORM identity maps. Hosted admission validates user/workspace ownership and keeps one publication quota slot across lost replies.

Reviewed-video-only bridges are excluded from generic plain-post live-sync discovery and history import. They use the same durable publication executor and provider upload checkpoints as direct approvals. Legacy direct approval requests remain backward compatible, while the hosted UI selects bridges. Automatic text adaptation and link-back are not silently applied to approved videos.

Bridge increment validation: 78 focused Core tests passed, followed by 27 final bridge/service checks including a stale identity-map case. Hosted API validation passed 69 tests, including generic connection and Postbridge-post regressions; all 26 hosted browser scenarios passed. The hosted dev API returns the new bridge view for the existing video draft; API health and workers were verified after rebuild. No fake social destinations were added to the user's workspace and no external publication was attempted.


## Hosted YouTube consent and data lifecycle

The hosted connection requires explicit acceptance of the Postbridge policies and YouTube terms, recorded with policy revision `2026-10-20`. Google sign-in retains its existing scopes; YouTube publishing is authorized separately. One channel cannot silently move between workspace tenants or owners, and first connections are serialized in PostgreSQL before storing credentials.

Disconnect revokes the Google token before erasing the encrypted credentials, channel metadata, saved upload sessions and historical upload IDs. User-authored source posts and videos remain. Dependent bridges/RSS must be removed before an ordinary user disconnect; a confirmed provider revocation also erases dependent API records. A failed revoke/delete keeps the registry entry available for retry.

The existing hosted beat checks due connections daily via the service-token-protected Core maintenance endpoint, in hourly batches of 50. It refreshes channel display metadata, detects `invalid_grant` or a missing authorized channel, and removes revoked data. Transient provider failures retry without immediately deleting a valid connection; stale API caches and provider receipts have a 29-day limit. Expired upload sessions are erased and cannot authorize another upload. Display names cached in past approvals may refresh; approved video titles, descriptions, audience, visibility, media and other destinations do not change.

Keep hosted beat and the Core API healthy for these controls. Real-account OAuth, upload, the public policy pages and Google's verification decision still require a production pilot; passing local tests does not establish Google approval.


## Batch production and 1080 export (October 2026)

All new exports are 1080×1920 at 24 fps. Existing files remain immutable. `POST /videos/batches` accepts 1–5 autopilot originals and up to six additional language/voice choices per original. `POST /videos/{id}/voice-batches` reuses a completed revision's visuals for 1–6 speech variants. A durable parent and ordered items link each production job, including nested voice batches. Listing, status, cancellation and retry after a lost HTTP acknowledgement use deterministic request keys. Each child is admitted immediately before work, with one active child per batch and tenant queue limits. A failed/uncertain paid step stops later items; successful outputs remain available.

Hosted children share the parent's total cap and, for scheduled work, the run/month caps. Held and uncertain amounts consume the remaining allowance until settlement. The actor and ownership are rechecked for each child. Project/version locks protect manual edits, and queued input assets cannot be deleted. Self-host batches use the existing funded gateway and cumulative schedule caps. Native model fees remain measured in generated seconds; ordinary speech/text retain their native token accounting.

Schedule templates add `count` and `variants`; each occurrence freezes them with its materials and limits. The existing agent scheduler, durable video queue and usage ledger own the whole operation. A missed occurrence never triggers a catch-up spending burst.

## Native AI video scenes

`VIDEO_GENERATION_ENABLED=1` enables the existing funded gateway's asynchronous Google Veo contract. `VIDEO_GENERATION_MODEL` is `veo-3.1-fast-generate-001` (1080p) or its explicit `:720p` price SKU. Both produce one silent vertical eight-second clip per Core scene. A shorter montage scene trims that generated clip, but billing still charges all generated seconds. Uploaded images, or the first frame of a managed clip, can seed a replacement scene. Audio is added by the existing narration stage. The default remains illustration generation.

Hosted native video prices come from the funded GitSell payer’s exact eight-second quote through `/internal/service/videos/generation-price`. `VIDEO_SCENE_RESERVE_UNITS` is deprecated and ignored. The preview and reservation use that GT quote without a Postbridge model coefficient. The operational allowance for other AI work is capped by the remaining workspace/run budget; unused holds are released. Voice-only variants do not regenerate visuals. An unpriced native SKU remains unavailable. The gateway independently freezes its exact scene quote and rejects admission above `max_units` before calling Vertex.

The gateway must first deploy `20261022_ai_proxy_video_jobs`, seed only the two new model configurations through the normal pricing process, and enable `AI_VIDEO_GENERATION_ENABLED`. API and scheduler/video workers share its private document volume. The request input and MP4 are private, retained up to 24 hours; a database usage receipt retains financial provenance. Billing uses the ordinary GitSell balance and the existing at-least-three-times-cost plan economics. No new customer provider account is required.

Core uses an immutable scene request UUID, yields the worker during polling and retrieves accepted work by that UUID after lost acknowledgements. Cancelling production stops new scenes; free polling and settlement of an already accepted scene continue, even when production flags are subsequently disabled. Paid completed clips are preserved in the tenant library where possible. Invalid paid outputs retain their known charge and stop. An unknown native submission acknowledgement holds its allowance for operator reconciliation and is never automatically submitted again.

Official contracts: [Veo text generation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-text), [Veo image generation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-an-image), [Google video pricing](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing), [Google speech pricing](https://cloud.google.com/text-to-speech/pricing).

## Explicit schedule publication policy

New schedules default to drafts. Optional `auto_publication` requires `confirmed: true`, selected bridge IDs, the reviewed bridge-setting hashes and an explicit common caption. The server records the policy with the creator and immutable occurrence snapshot. It can only use active bridges from that task's editorial source. TikTok remains a per-video review flow and is excluded from standing consent.

Completed originals and voice variants prepare their own canonical post drafts. Hosted work rechecks the creator's current role, workspace/bridge ownership and subscription quota through the same publication admission service used by the editor. Core rechecks the active task and unchanged policy, generated revision set, post text and bridge hashes before committing each immutable approval. Each approval retains its agent run ID. Pausing/archiving the task or changing its bridges stops pending automatic deliveries in the shared executor. Already sent publications remain sent. A changed post or project requires manual review; no automatic overwrite or paid regeneration occurs.

One bounded approval is processed per completion callback, using the existing outbox and periodic dispatcher. Replays reuse prepared posts, quota admissions and approvals. History distinguishes pending approval, approved delivery and a stop requiring review. Approval does not imply the external platform has successfully published: ordinary per-target delivery statuses remain authoritative.

## Hosted video campaigns

The hosted `/content/video-campaigns` screen groups completed revisions with labels, optional promo codes and optional manual-work minutes. It shows deduplicated production charges (shared source visuals count once), publication targets, available YouTube views/likes/comments and attributed conversion counts. Other platform counters are explicitly unavailable until their official integrations support them. YouTube counters retain their native definitions and are not treated as unique viewers. Provider snapshots refresh at most once per 15 minutes and expire after 29 days.

A campaign's shared tracking URL records campaign-only link requests. Per-variant URLs or unique promo codes are required to attribute a specific video. Redirect requests and signed website visits are separate event types; neither is represented as a unique person. External website events use `POST /video-attribution/events`, signed with a one-time workspace key. Send HMAC SHA-256 over `timestamp + "." + raw_json` in `X-Postbridge-Signature` and Unix seconds in `X-Postbridge-Timestamp` (five-minute tolerance). Required fields: `campaign_id`, opaque `event_id`, `event_type`, ISO-8601 `occurred_at`; optional `variant_id` or `promo_code`. Types: `visit`, `registration`, `activation`, `reuse`, `payment`. Payment events may supply positive `amount_minor` and an ISO currency together. Event IDs deduplicate retries; changed payloads conflict. Do not send personal data.

Platform-admin campaigns can opt into internal Postbridge attribution for a Postbridge landing page. A signed first-touch reference is kept in the current browser tab until authenticated claim (server expiry 30 days). Only a newly registered user can claim it. Activation and repeat use come from Core-confirmed publication receipts in newly owned workspaces, not browser claims. Fan-out of one plan is one activation; reuse requires another plan on a different UTC day. Payments require successful, non-disputed, not fully refunded backend receipts; trials do not qualify. Incomplete reconciliation and unconnected external event types remain unknown rather than zero. The report does not declare a winning creative from views or a small conversion sample.

Hosted migrations: `20261022_video_batch_budget`, then `20261022_video_campaigns`. Core migrations: `20261022_video_batches`, then `20261022_video_auto_pub`. The Nginx/Vite routes must pass `/video-attribution/*` and workspace `/video-campaigns/*` to SaaS. Production authenticates workspace actions with JWT; `X-User-Id` is a dev/test fallback only. No external OAuth scope is added by campaign metrics.


## Schedule and narration follow-ups (23 October 2026)

`GET /videos/schedules` returns video templates with `template_hash`. `PATCH /videos/schedules/{id}` accepts that hash, a new immutable request key, the brief, cron/timezone and full validated configuration. A replay of the same edit returns the same task; a conflicting edit is rejected. Archived schedules cannot be edited. Paused schedules remain paused. Hosted callers use the authenticated editor, never a supplied actor ID. Current runs retain their material/limit/actor snapshots. Changed automatic-publication consent stops older pending approvals.

`material_folder` (including the empty root-folder name) and `material_limit` select the newest material assets at each occurrence. Dynamic inputs require autopilot without fixed scenes. Selection is tenant-scoped and frozen before paid work; missing inputs produce `VIDEO_FOLDER_EMPTY`. Optional `auto_publication.language_bridge_ids` maps every produced language to nonempty subsets of the explicitly reviewed bridges. Each revision is approved only for its language’s destinations; TikTok remains outside standing consent.

The UI provides six static Google voice samples (RU/EN × Kore/Charon/Puck). Samples use neutral delivery and playback incurs no new gateway request. Generated narration stores reusable acoustic word cues on `video_speech_assets.alignment_json`. The video worker runs pinned faster-whisper 1.2.1 / Whisper small (CPU int8) against the known text in a bounded subprocess, offline after the image build. Model revision: `536b0662742c02347bc0e980a01041f333bce120`. `VIDEO_ALIGNMENT_MODEL_PATH` can override the built-in `/opt/postbridge-models/whisper-small` directory. Invalid/missing alignment falls back to phrase subtitles. See the [upstream alignment implementation](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py).

A translated scene exceeding the verified scene/total audio duration may use one checkpointed shortening request and one new synthesis. Each accepted attempt remains charged; unknown requests are never retried. If the shorter text still does not fit, production stops for review. The edited transcript is marked in the UI; visuals are reused.

Native image seeds are full-resolution JPEGs, compressed locally to at most 650 KiB so their base64 request fits a 1 MiB gateway ingress limit. HTTP 413/415 now definitively reject admission instead of causing endless polling. The real production acceptance retained one completed 1080p scene and generated an image-seeded replacement; each generated eight-second scene charged 321,071 GT, with no repeat charge for the reused scene. The assembled output is private and was not published to a social network.

Verified completed visual checkpoints remain visible in the storyboard even when another scene fails. The read view overlays owned completed assets without changing the immutable original request. Regeneration flags and the old source trim are cleared for those assets, so a new revision can reuse a paid scene and generate only the missing neighbour. Pending storyboard scenes keep their prompts and positions.


## Agent and MCP configuration (24 October 2026)

Hosted video schedules use a plain-language request and review card. The page retains schedule status, history and a direct pause action; it does not expose forms for folders, language routing, batch size, budgets or advanced cadence. The existing self-host form is retained because that deployment has no hosted assistant.

The SDK assistant can create a schedule from a brief without an open project, inspect complete schedule templates, and propose an in-place edit using the current hash. Dynamic folders, original count, voice/language variants, notifications, run/month budgets and explicit language destinations use the same validated configuration as HTTP. Publication fingerprints are read by the server and the exact recipients/languages appear in the saved card. Applying a card rechecks the actor, current template and bridge policy. A paused task stays paused.

Hosted MCP exposes video workspace capabilities, projects/storyboards, material folders, full schedules and eligible bridges; create/edit/voice operations and schedule creation/edit/control prepare the same persisted assistant cards. `apply_video_action` requires explicit approval of the returned preview and reserve. MCP uses workspace OAuth scopes, membership/role checks, request idempotency and the existing budget and usage services. Even free montage edits wait for review through MCP. Automatic publication additionally requires the publishing OAuth scope. No separate video price or billing multiplier is introduced. Word subtitles and bounded translated-speech shortening remain automatic implementation behavior, not user settings.


## Agent controls, deadlines and covers (24 October 2026)

The hosted SDK assistant and MCP share persisted review cards for original/voice batches,
batch and job cancellation, library renames/moves, and exact-version delivery approval,
rescheduling, cancellation, retry and reconciliation. Reads expose owned projects,
batches, current publication snapshots and fresh TikTok creator options. Applying a
saved card rechecks workspace membership, publication scope, prices and stale snapshots.
TikTok still needs explicit per-video declarations; schedules cannot grant them.

`production_lead_minutes` (5–1440, optional) makes a video's existing cron describe
publication time. Production starts that many elapsed minutes earlier. Every occurrence
freezes its UTC publication instant and timezone before any AI work, including across
DST changes. A nonexistent local slot is skipped; an overlapping hour uses its first
occurrence only. Legacy templates without a lead retain their generation-time semantics.
Overdue unstarted occurrences incur no AI cost. A late result is retained for review,
never automatically sent at a substituted time. Hosted notifications use the existing
durable outbox for an overdue active production and for expired delivery targets, with
one notice per occurrence/deadline version and current workspace membership checks.

Every new completed revision also stores a separate 720×1280 JPEG cover from its first
verified visual, scene caption and brand color. It incurs no extra provider call, is
available as `current.cover_url`/`cover_title`, and becomes the post draft's cover. The
private production brief is never used as cover text. Covers share managed-media
ownership, retention and cleanup rules with previews and remain bound to their revision.

Storyboard/image/speech steps now send the remaining GitSell allowance to
`/v1/bounded/{chat/completions,images/generations,audio/generations}`. The gateway checks
locked billing rates against a conservative input bound and an enforced provider output
limit before egress. Unsupported models/features or an older gateway fail closed;
known pre-provider rejections have zero charge and no unknown receipt. Actual provider
usage still determines the GitSell charge. Native video retains its existing frozen
payer quote and `max_units` contract. Deploy the GitSell bounded endpoints before this
Core increment. No migration or additional end-user settings are required.
