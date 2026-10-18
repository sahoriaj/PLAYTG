NOTE: This folder is an OPTIONAL alternative. The default big-file method now runs on your hosting (tgfiles/stream.php, see ../README.txt).

LARGE-FILE STREAMER (files from 20 MB up to 2 GB)

WHY: Telegram's Bot API lets bots download only 20 MB. Over MTProto a bot can read up to 2 GB.
This small Python server does that. It cannot run on shared cPanel hosting - it needs an
always-on machine: any cheap VPS, a free-tier VM, a Raspberry Pi or a PC at home.
Small files (<= 20 MB) keep working exactly as before and never touch this server.

FLOW: user opens link -> Cloudflare Worker -> (file > 20 MB) -> this streamer -> Telegram.
The Worker adds a secret header, so only your Worker can use the streamer.

1) Get API_ID and API_HASH: log in at https://my.telegram.org > API development tools > create an app.
2) On the server (Python 3.9+):
      cd streamer
      python3 -m venv venv && . venv/bin/activate
      pip install -r requirements.txt
      cp .env.example .env     # edit it: API_ID, API_HASH, BOT_TOKEN, STREAM_KEY (random, letters+numbers, 30+ chars)
      set -a; . ./.env; set +a; python server.py
   Check:  curl http://127.0.0.1:8080/health   -> ok
   Keep it running with systemd / pm2 / screen (e.g. systemd unit with EnvironmentFile=/path/.env).
3) Give it a public HTTPS address. Easiest and free: Cloudflare Tunnel (no open ports needed):
      cloudflared tunnel --url http://127.0.0.1:8080          (quick test, random trycloudflare.com address)
   or a named tunnel with your own hostname, e.g. https://stream.yourdomain.com  (recommended, stays fixed).
   Use standard HTTPS (port 443); the Worker cannot reliably reach odd ports.
4) Cloudflare Worker > Settings > Variables and Secrets, add:
      STREAM_URL  (Text)    = https://stream.yourdomain.com
      STREAM_KEY  (Secret)  = same STREAM_KEY as in .env
   Paste the new worker/worker.js and Deploy.
5) tgfiles/config.php: set STREAM_URL to the same address. Upload config.php and bot.php again.
   Open setup.php once more - it should show "Large-file streamer is reachable". Then delete setup.php.

NOTES
 - Telegram caps bot-readable files at 2 GB.
 - Do not run two copies with the same session folder.
 - Speed depends on your server's connection (Worker -> streamer -> Telegram), not on Cloudflare.
 - If you run the streamer on a different Telegram bot than the one users talk to, links will not work:
   file_ids only work for the bot that received the file. Use the same BOT_TOKEN everywhere.

======================================================================
NO VPS AND NO PC?  TWO FREE ROUTES  (free-tier rules change - check them)
======================================================================
A) ANDROID PHONE (free, no card) - Termux
   Install Termux from F-Droid (not the old Play Store version), then:
      pkg update && pkg install python git cloudflared
      pip install kurigram aiohttp          (tgcrypto is optional; it may fail to build - it only makes it faster)
      export API_ID=... API_HASH=... BOT_TOKEN=... STREAM_KEY=...
      python server.py &
      cloudflared tunnel --url http://127.0.0.1:8080
   It prints an https://xxxx.trycloudflare.com address -> use it as STREAM_URL (Worker + config.php).
   That address CHANGES every time you restart cloudflared; for a fixed address use a named tunnel
   (needs a free Cloudflare account + a domain on Cloudflare). Run `termux-wake-lock` and disable
   battery optimisation for Termux so Android doesn't kill it. Large links work only while the phone is on.

B) RENDER.COM free web service (no card, but sleeps and has a small bandwidth allowance)
   Put the streamer folder in a GitHub repo (GitHub's website lets you upload files from a phone).
   Render > New > Web Service > connect the repo > Runtime: Docker > Instance type: Free.
   Environment variables: API_ID, API_HASH, BOT_TOKEN, STREAM_KEY.
   Render gives you https://yourname.onrender.com -> that is STREAM_URL (no tunnel needed).
   Catch: it sleeps after ~15 min idle (first click after that waits up to a minute) and free bandwidth
   is small, so it suits testing / light use.
