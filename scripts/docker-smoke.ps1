param(
    [string]$Image = "offerpilot:smoke"
)

$ErrorActionPreference = "Stop"

docker build -t $Image .

# Run the same command the image exposes through ENTRYPOINT: oc smoke.
docker run --rm `
    -e OFFERPILOT_DATA=/tmp/offerpilot-smoke `
    $Image `
    smoke --static-dir /app/web/dist
