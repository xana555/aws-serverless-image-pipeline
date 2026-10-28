"""Starter Lambda.

Triggered by SQS. Each SQS message body is an S3 event notification.
For every uploaded object it starts one Step Functions execution with:
    {"bucket": "<source bucket>", "key": "<object key>"}

Environment variables:
    STATE_MACHINE_ARN   ARN of the image-pipeline-workflow state machine
"""
import json
import os

import boto3

sfn = boto3.client("stepfunctions")
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"].strip()


def lambda_handler(event, context):
    started = 0

    for record in event["Records"]:
        body = json.loads(record["body"])

        # S3 sends a harmless test message when the notification is created
        if body.get("Event") == "s3:TestEvent":
            continue

        for s3_record in body.get("Records", []):
            bucket = s3_record["s3"]["bucket"]["name"]
            key = s3_record["s3"]["object"]["key"]  # URL-encoded; the processor decodes it

            sfn.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                input=json.dumps({"bucket": bucket, "key": key}),
            )
            started += 1

    # If start_execution raises, the message returns to the queue and is
    # retried. After 3 failed receives it moves to the dead-letter queue.
    return {"started": started}
