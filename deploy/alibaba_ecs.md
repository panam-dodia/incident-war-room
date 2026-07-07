# Deploying to Alibaba Cloud ECS

This is the deployment path for the hackathon's required "Proof of Alibaba Cloud Deployment"
recording. The app is a single Docker image (FastAPI backend + static frontend), so the
deployment is intentionally minimal: one small ECS instance running one container.

## 1. Provision an ECS instance

1. In the Alibaba Cloud console, create an ECS instance (a `ecs.t6-c1m1.large` / 1 vCPU / 2 GiB
   burstable instance is plenty — this app does no heavy local compute, it just calls Qwen Cloud).
2. Choose a public image with Docker preinstalled, or Ubuntu 22.04 and install Docker yourself:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```
3. Open port 8000 (or your chosen port) in the instance's security group.

## 2. Ship the image

From your machine, at the repo root:

```bash
docker build -t incident-war-room .
docker save incident-war-room | gzip > incident-war-room.tar.gz
scp incident-war-room.tar.gz <user>@<ecs-public-ip>:~/
```

(Alternatively, push to Alibaba Cloud Container Registry (ACR) and `docker pull` on the
instance — cleaner if you'll redeploy more than once.)

## 3. Run it on the instance

```bash
ssh <user>@<ecs-public-ip>
docker load < incident-war-room.tar.gz
docker run -d --name war-room -p 8000:8000 \
  -e QWEN_API_KEY=<your-key> \
  -e QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  incident-war-room
```

Leave `QWEN_API_KEY` unset to run in mock mode on the instance too (useful to first confirm the
deployment itself works before spending credits).

## 4. Confirm it's live

```bash
curl http://<ecs-public-ip>:8000/api/health
# {"status": "ok", "mock_mode": false}
```

Open `http://<ecs-public-ip>:8000` in a browser to see the dashboard served from Alibaba Cloud.

## 5. Record the proof

The hackathon requires a short recording, separate from the main demo video, proving the
backend runs on Alibaba Cloud. Suggested recording (30-60s is plenty):

1. Show the Alibaba Cloud ECS console with the running instance (region/instance ID visible).
2. In a terminal, `curl http://<ecs-public-ip>:8000/api/health` showing a response from the
   public IP.
3. Open the dashboard in a browser at that IP and run one incident live.
4. Point to `backend/app/qwen_client.py` in the repo as the code that calls Qwen Cloud
   (DashScope), which is the Alibaba Cloud service this project depends on.

Submit that recording alongside a link to `backend/app/qwen_client.py` in the repo, per the
submission requirements.
