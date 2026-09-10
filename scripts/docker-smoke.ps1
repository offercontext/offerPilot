param(
    [string]$Image = "offerpilot:smoke"
)

$ErrorActionPreference = "Stop"

docker build -t $Image .
if ($LASTEXITCODE -ne 0) { throw "Docker build failed (exit $LASTEXITCODE)." }

# Run the same command the image exposes through ENTRYPOINT: oc smoke.
docker run --rm `
    -e OFFERPILOT_DATA=/tmp/offerpilot-smoke `
    $Image `
    smoke --static-dir /app/web/dist
if ($LASTEXITCODE -ne 0) { throw "Docker smoke failed (exit $LASTEXITCODE)." }
