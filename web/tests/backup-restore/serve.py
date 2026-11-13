"""Disposable synthetic workspace; production HTTP app, no provider configuration."""
import os
from pathlib import Path
import sys

os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'

import uvicorn
from offerpilot.api import create_app

app = create_app(data_dir=Path(sys.argv[1]), static_dir=Path(sys.argv[3]))


@app.middleware('http')
async def fixture_identity(request, call_next):
    response = await call_next(request)
    response.headers['X-Restore-Fixture'] = sys.argv[4]
    return response


uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), log_level='warning')
