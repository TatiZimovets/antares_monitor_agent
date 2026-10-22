# AWS Lambda container image for the Antares monitor agent.
#
# Build:
#   docker build -t antares-monitor .
# Smoke-test locally (uses AWS RIE built into the base image):
#   docker run --rm -p 9000:8080 --env-file .env antares-monitor
#   curl -s http://localhost:9000/2015-03-31/functions/function/invocations -d '{}'
#
# Push to ECR & wire up Lambda via console. Handler: src.handler.lambda_handler

FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY src/ ${LAMBDA_TASK_ROOT}/src/
COPY prompts/ ${LAMBDA_TASK_ROOT}/prompts/
COPY config/ ${LAMBDA_TASK_ROOT}/config/

CMD ["src.handler.lambda_handler"]
