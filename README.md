# 🗺️ City Events Finder
**INFO 520 – Data Communications | VCU Business**  
**Instructor:** Promod Sreedharan

A cloud-native web app that aggregates upcoming city events from **3 external APIs**, deployed on **Google Cloud Run** with an **HTTP(S) Load Balancer**.

---

## 🏗️ Architecture

```
User (Browser)
     │
     ▼
External HTTP(S) Load Balancer  (GCP)
     │
     ▼
Cloud Run Service  (auto-scaling, containerized)
     │
     ├──► Ticketmaster Discovery API
     ├──► PredictHQ Events API
     └──► OpenStreetMap Overpass API (no key needed)
```

---

## 🚀 Quick Start (Local)

```bash
# 1. Clone
git clone https://github.com/<your-repo>/city-events-finder.git
cd city-events-finder

# 2. Set API keys
export TICKETMASTER_API_KEY=your_key_here
export PREDICTHQ_API_KEY=your_key_here

# 3. Install & run
pip install -r requirements.txt
python app.py

# 4. Visit http://localhost:8080
```

---

## 🐳 Docker (Local Test)

```bash
docker build -t city-events-finder .
docker run -p 8080:8080 \
  -e TICKETMASTER_API_KEY=your_key \
  -e PREDICTHQ_API_KEY=your_key \
  city-events-finder
```

---

## ☁️ GCP Deployment (Step-by-Step)

### Prerequisites
- GCP project with billing enabled
- `gcloud` CLI installed and authenticated

### Step 1 – Enable APIs
```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  compute.googleapis.com
```

### Step 2 – Store Secrets in Secret Manager
```bash
echo -n "YOUR_TICKETMASTER_KEY" | gcloud secrets create ticketmaster-api-key --data-file=-
echo -n "YOUR_PREDICTHQ_KEY"    | gcloud secrets create predicthq-api-key    --data-file=-
```

### Step 3 – Create Artifact Registry & Push Image
```bash
export PROJECT_ID=$(gcloud config get-value project)
export REGION=us-east1

# Create repo
gcloud artifacts repositories create city-events-repo \
  --repository-format=docker \
  --location=$REGION

# Build & push
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT_ID/city-events-repo/city-events-finder:latest
```

### Step 4 – Deploy to Cloud Run
```bash
gcloud run deploy city-events-finder \
  --image $REGION-docker.pkg.dev/$PROJECT_ID/city-events-repo/city-events-finder:latest \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --port 8080 \
  --min-instances 0 \
  --max-instances 10 \
  --set-env-vars GCP_PROJECT_ID=$PROJECT_ID \
  --set-secrets TICKETMASTER_API_KEY=ticketmaster-api-key:latest,PREDICTHQ_API_KEY=predicthq-api-key:latest
```

### Step 5 – Set Up HTTP(S) Load Balancer
```bash
# Create a serverless NEG pointing to Cloud Run
gcloud compute network-endpoint-groups create city-events-neg \
  --region=$REGION \
  --network-endpoint-type=serverless \
  --cloud-run-service=city-events-finder

# Backend service
gcloud compute backend-services create city-events-backend \
  --load-balancing-scheme=EXTERNAL_MANAGED \
  --global

gcloud compute backend-services add-backend city-events-backend \
  --network-endpoint-group=city-events-neg \
  --network-endpoint-group-region=$REGION \
  --global

# URL map → target proxy → forwarding rule
gcloud compute url-maps create city-events-urlmap \
  --default-service city-events-backend

gcloud compute target-http-proxies create city-events-proxy \
  --url-map city-events-urlmap

gcloud compute forwarding-rules create city-events-lb \
  --load-balancing-scheme=EXTERNAL_MANAGED \
  --network-tier=PREMIUM \
  --global \
  --target-http-proxy city-events-proxy \
  --ports 80
```

### Step 6 – Verify Health Check
```bash
# Get Load Balancer IP
gcloud compute forwarding-rules describe city-events-lb --global --format="get(IPAddress)"

# Test health endpoint
curl http://<LB_IP>/health
# Expected: OK
```

---

## 🔒 Security
- API keys stored in **GCP Secret Manager** — never hardcoded
- Docker image runs as non-root user
- HTTPS-ready via Load Balancer SSL certificate

---

## 📡 API Sources

| Source | Key Required | Free Tier |
|--------|-------------|-----------|
| [Ticketmaster Discovery](https://developer.ticketmaster.com/) | Yes | 5,000 req/day |
| [PredictHQ](https://www.predicthq.com/) | Yes | 100 req/day |
| [OpenStreetMap Overpass](https://overpass-api.de/) | No | Unlimited |

---

## 📁 Project Structure
```
city-events-finder/
├── app.py              # Flask backend + HTML frontend
├── requirements.txt    # Python dependencies
├── Dockerfile          # Multi-stage Docker build
└── README.md           # This file
```

---

## 🤖 GenAI Usage
Anthropic. (2026, April 13). *Build a City Events Finder Flask app with GCP Cloud Run and Load Balancer deployment* [Generative AI chat]. Claude (claude-sonnet-4-6). https://claude.ai
