#!/bin/sh

if [ "$USE_DDTRACE" = "true" ]; then
    export DD_TRACE_OPENAI_ENABLED="False"
    exec ddtrace-run python -c 'import litellm; litellm.run_server()' "$@"
else
    exec python -c 'import litellm; litellm.run_server()' "$@"
fi
