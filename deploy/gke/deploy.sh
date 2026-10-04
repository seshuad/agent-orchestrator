#!/usr/bin/env bash
# Build the image with Cloud Build and run the designer on a GKE Autopilot cluster, as cheaply as GKE allows:
# one spot pod, a 10 GB disk, no load balancer. Safe to run again: it updates what's there.
#   PROJECT=my-project deploy/gke/deploy.sh          (ANTHROPIC_API_KEY in the environment is stored as a secret)
set -euo pipefail
cd "$(dirname "$0")/../.."

PROJECT=${PROJECT:-$(gcloud config get-value project 2>/dev/null)}
REGION=${REGION:-us-central1}
CLUSTER=${CLUSTER:-agent-orchestrator}
REPO=${REPO:-agent-orchestrator}
NETWORK=${NETWORK:-agent-orchestrator}
TAG=${TAG:-$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- . || echo "-$(date +%Y%m%d%H%M%S)")}
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/agent-orchestrator:$TAG"
GSA_NAME=agent-orchestrator
GSA="$GSA_NAME@$PROJECT.iam.gserviceaccount.com"
NS=agent-orchestrator
say() { printf '\n== %s\n' "$*"; }

say "APIs"
gcloud services enable compute.googleapis.com container.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  bigquery.googleapis.com storage.googleapis.com pubsub.googleapis.com --project "$PROJECT"

say "Image $IMAGE"
gcloud artifacts repositories describe "$REPO" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" --project "$PROJECT"
[ -n "${SKIP_BUILD:-}" ] || gcloud builds submit --tag "$IMAGE" --project "$PROJECT" --region "$REGION" --machine-type e2-highcpu-8 .

say "Network $NETWORK"
gcloud compute networks describe "$NETWORK" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud compute networks create "$NETWORK" --subnet-mode custom --project "$PROJECT"
gcloud compute networks subnets describe "$NETWORK-$REGION" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud compute networks subnets create "$NETWORK-$REGION" --network "$NETWORK" --region "$REGION" \
    --range 10.10.0.0/20 --enable-private-ip-google-access --project "$PROJECT"

say "Cluster $CLUSTER (Autopilot)"
gcloud container clusters describe "$CLUSTER" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud container clusters create-auto "$CLUSTER" --region "$REGION" --project "$PROJECT" \
    --network "$NETWORK" --subnetwork "$NETWORK-$REGION"
gcloud container clusters get-credentials "$CLUSTER" --region "$REGION" --project "$PROJECT"

say "Google access for the pod: $GSA"
gcloud iam service-accounts describe "$GSA" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "$GSA_NAME" --display-name "Agent orchestrator on GKE" --project "$PROJECT"
for role in roles/bigquery.jobUser roles/bigquery.dataViewer roles/storage.objectViewer roles/pubsub.subscriber; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$GSA" --role "$role" --condition None >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$GSA" --project "$PROJECT" --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:$PROJECT.svc.id.goog[$NS/agent-orchestrator]" >/dev/null

say "Kubernetes"
IMAGE="$IMAGE" GSA="$GSA" envsubst '${IMAGE} ${GSA}' < deploy/gke/k8s.yaml | kubectl apply -f -
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  kubectl -n "$NS" create secret generic anthropic --from-literal api-key="$ANTHROPIC_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "$NS" rollout restart deployment/agent-orchestrator
fi
kubectl -n "$NS" rollout status deployment/agent-orchestrator --timeout 15m

say "Running"
cat <<MSG
Open it (no public address; this forwards a local port to the pod):
  kubectl -n $NS port-forward svc/agent-orchestrator 8701:8700
  open http://localhost:8701
Copy the laptop's agents and connectors over: deploy/gke/copy-workspace.sh
MSG
