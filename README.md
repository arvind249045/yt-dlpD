# Instagram yt-dlp API

A local, authenticated download service intended for n8n. It accepts Instagram profile, Reels-tab, post, and Reel URLs; runs downloads as background jobs; preserves metadata; and skips media already recorded in the shared download archive.

Note: yt-dlp currently labels its Instagram whole-user extractor as broken. Single Reel/post URLs work, and bulk jobs can use the `urls` array below. Profile discovery therefore needs a separate source when Instagram blocks the user extractor.

## Start

Open two PowerShell windows in this folder:

```powershell
.\start.ps1
```

```powershell
.\start-tunnel.ps1
```

The second window prints a temporary `https://...trycloudflare.com` base URL. Keep both windows open. Quick Tunnel URLs change whenever the tunnel restarts.

For Instagram login, close the API, set `COOKIE_BROWSER=edge` (or `chrome`/`firefox`) in `.env`, sign into Instagram in that browser, close the browser fully, and restart the API. Browser-cookie access is local; never send cookies to n8n.

## n8n request

Use an HTTP Request node:

- Method: `POST`
- URL: `https://YOUR-TUNNEL.trycloudflare.com/v1/jobs`
- Header: `Authorization: Bearer YOUR_API_KEY_FROM_.ENV`
- JSON body:

```json
{
  "urls": [
    "https://www.instagram.com/reel/SHORTCODE_1/",
    "https://www.instagram.com/reel/SHORTCODE_2/"
  ],
  "mode": "audio",
  "max_items": 1000
}
```

Poll the returned `status_url` with the same Authorization header until `status` is `completed` or `completed_with_errors`, then GET `items_url`. Each item includes the post URL, available engagement fields, caption, and an authenticated file download URL.

## Endpoints

- `GET /health` — public liveness check
- `POST /v1/jobs` — submit a download
- `GET /v1/jobs/{id}` — job status
- `GET /v1/jobs/{id}/items` — structured results
- `GET /v1/jobs/{id}/files/{filename}` — media file
- `GET /v1/jobs/{id}/log` — troubleshooting log

Only use this with content you are authorized to access. Instagram may rate-limit automation; the service deliberately spaces requests and supports resumable runs.

## Host on Railway

Deploy this directory as a Docker service, then configure:

- Variable `API_KEY`: a long random secret
- Variable `DOWNLOAD_ROOT`: `/data`
- Persistent volume mounted at `/data`
- Public domain generated for the service

Use the generated HTTPS domain in n8n. A hosted server cannot read cookies from a browser on your PC, so `COOKIE_BROWSER` must remain empty there. Authenticated Instagram access would require a separately provisioned cookie file or session mechanism and careful secret handling.
