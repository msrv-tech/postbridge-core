# Content studio samples

The homepage uses a fictional coffee-shop campaign: a sample post, an AI-generated photograph, and two language versions of a video. It makes no claims about a real business, its prices, opening hours, or location.

## Current coffee examples

- `coffee-cover.jpg`: a 960×720 JPEG export of an image generated with Codex's built-in imagegen tool on 30 October 2026. The page labels it “AI-generated”; it is not attributed to the Postbridge image pipeline.
- `coffee-video-ru.mp4` and `coffee-video-en.mp4`: twelve-second exports from the production Postbridge video studio. Two native Veo 3.1 Fast scenes show espresso preparation and a cappuccino with a croissant. Postbridge adds Google Kore narration and subtitles. The English version reuses the original scene assets rather than regenerating the visuals.
- `coffee-video-ru-poster.jpg` and `coffee-video-en-poster.jpg`: frames extracted at 4.5 seconds from the corresponding finished exports, after the first subtitle has finished.

The web videos are 540×960 H.264/AAC copies with fast-start metadata. The original 1080×1920 exports are retained with the release artifacts outside the public repository. Playback is user-initiated, with `preload="none"`. Switching the page language replaces the video element so its narration, captions, and poster all match the selected language.

## Image prompt

Use case: ads-marketing. Asset type: photographic cover for a coffee shop social campaign, shown in a small website card. A photorealistic advertising photograph of morning coffee and a freshly baked croissant in a warm neighborhood cafe. One matte warm ivory ceramic cappuccino cup and matching saucer, delicate realistic latte art, a single golden flaky croissant on a small simple cream plate. Oak table near a window, soft honey morning light, gently blurred cafe background. Editorial food photography, tactile pastry layers, restrained and appetizing, true-to-life materials. Cozy, inviting, premium but unpretentious. Landscape 4:3 frame; tight natural three-quarter view, cup and croissant both clearly visible and large, balanced composition legible at thumbnail size. Subtle steam. No text, logos, watermarks, people, hands, extra cups, extra pastries, collages, or graphic overlays.

## Video prompts

Shared style: Photorealistic editorial food advertising. Warm natural morning light, honey oak, ivory ceramic, golden pastry, shallow depth of field. Restrained, inviting, tactile, no stylized CGI or text.

### Scene 1

Vertical 9:16 cinematic macro food photography. A narrow stream of rich espresso flows from the spout of a professional espresso machine into one matte warm ivory ceramic cup resting on the drip tray. Realistic golden crema blooms as the coffee fills, delicate wisps of steam, warm morning light, soft cafe bokeh. The cup stays still and has a realistic shape and one handle. Focus on the beautiful liquid and ceramic texture. One continuous shot with a very slow gentle camera push. No people, no hands, no text, no logos, no extra cups. Keep the cup and coffee stream in the central two-thirds with room above and below for captions.

### Scene 2

Vertical 9:16 photorealistic food advertising film. One warm ivory matte ceramic cappuccino cup with delicate latte art on its matching saucer, and one golden flaky croissant on a small cream plate, placed together on a honey oak table beside a cafe window. Beautiful natural morning sunlight with soft shadows. Slow subtle camera dolly toward the breakfast, faint realistic steam from coffee, tactile crisp pastry layers, creamy coffee foam, softly blurred cozy cafe background. Both cup and croissant stay fully visible in the central frame. One continuous steady shot, no hands or people, no text or logos, no extra cups or pastries. Leave the very top and bottom unobstructed for captions.

## Legacy assets

`generated-video.mp4`, `generated-video-poster.jpg`, and `generated-cover.jpg` are earlier studio acceptance samples from 22–24 October 2026. They remain available for previously cached pages; the current homepage uses the coffee campaign above.
