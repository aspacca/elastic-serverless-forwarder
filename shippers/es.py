# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.

import hashlib
import json
from typing import Any, Dict, Optional, Union

import elasticapm  # noqa: F401
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk as es_bulk

from share import shared_logger

from .shipper import CommonShipper, ReplayHandlerCallable


class ElasticsearchShipper(CommonShipper):
    """
    Elasticsearch Shipper.
    This class implements concrete Elasticsearch Shipper
    """

    def __init__(
        self,
        elasticsearch_url: str = "",
        username: str = "",
        password: str = "",
        cloud_id: str = "",
        api_key: str = "",
        dataset: str = "",
        namespace: str = "",
        tags: list[str] = [],
        batch_max_actions: int = 500,
        batch_max_bytes: int = 10 * 1024 * 1024,
    ):

        self._bulk_actions: list[dict[str, Any]] = []

        self._bulk_batch_size = batch_max_actions

        self._bulk_kwargs: dict[str, Any] = {
            "max_retries": 10,
            "stats_only": False,
            "raise_on_error": False,
            "raise_on_exception": False,
            "max_chunk_bytes": batch_max_bytes,
        }

        if batch_max_actions > 0:
            self._bulk_kwargs["chunk_size"] = batch_max_actions

        es_client_kwargs: dict[str, Any] = {}
        if elasticsearch_url:
            es_client_kwargs["hosts"] = [elasticsearch_url]
        elif cloud_id:
            es_client_kwargs["cloud_id"] = cloud_id
        else:
            raise ValueError("You must provide one between elasticsearch_url or cloud_id")

        if username:
            es_client_kwargs["http_auth"] = (username, password)

        elif api_key:
            es_client_kwargs["api_key"] = api_key
        else:
            raise ValueError("You must provide one between username and password or api_key")

        self._replay_args: dict[str, Any] = {}

        self._es_client = self._elasticsearch_client(**es_client_kwargs)
        self._replay_handler: Optional[ReplayHandlerCallable] = None

        self._dataset = dataset
        self._namespace = namespace
        self._tags = tags

    @staticmethod
    def _elasticsearch_client(**es_client_kwargs: Any) -> Elasticsearch:
        """
        Getter for elasticsearch client
        Extracted for mocking
        """

        es_client_kwargs["timeout"] = 30
        es_client_kwargs["max_retries"] = 10
        es_client_kwargs["retry_on_timeout"] = True
        return Elasticsearch(**es_client_kwargs)

    @staticmethod
    def _s3_object_id(event_payload: dict[str, Any]) -> str:
        """
        Port of
        https://github.com/elastic/beats/blob/21dca31b6296736fa90fae39bff71f063522420f/x-pack/filebeat/input/awss3/s3_objects.go#L364-L371
        https://github.com/elastic/beats/blob/21dca31b6296736fa90fae39bff71f063522420f/x-pack/filebeat/input/awss3/s3_objects.go#L356-L358
        """
        offset: int = event_payload["fields"]["log"]["offset"]
        bucket_arn: str = event_payload["fields"]["aws"]["s3"]["bucket"]["arn"]
        object_key: str = event_payload["fields"]["aws"]["s3"]["object"]["key"]

        src: str = f"{bucket_arn}{object_key}"
        hex_prefix = hashlib.sha256(src.encode("UTF-8")).hexdigest()[:10]

        return f"{hex_prefix}-{offset:012d}"

    def _enrich_event(self, event_payload: dict[str, Any]) -> None:
        """
        This method enrich with default metadata the ES event payload.
        Currently hardcoded for logs type
        """

        event_payload["data_stream"] = {
            "type": "logs",
            "dataset": self._dataset,
            "namespace": self._namespace,
        }

        event_payload["event"] = {"dataset": self._dataset, "original": event_payload["fields"]["message"]}

        event_payload["tags"] = ["preserve_original_event", "forwarded", self._dataset.replace(".", "-")] + self._tags

    def _handle_outcome(self, errors: tuple[int, Union[int, list[Any]]]) -> None:
        assert isinstance(errors[1], list)

        success = errors[0]
        failed = len(errors[1])
        for error in errors[1]:
            action_failed = [action for action in self._bulk_actions if action["_id"] == error["create"]["_id"]]
            assert len(action_failed) == 1
            if self._replay_handler is not None:
                self._replay_handler("elasticsearch", self._replay_args, action_failed[0])

        if failed > 0:
            shared_logger.error("elasticsearch shipper", extra={"success": success, "failed": failed})
            return

        shared_logger.info("elasticsearch shipper", extra={"success": success, "failed": failed})

        return

    def set_replay_handler(self, replay_handler: ReplayHandlerCallable) -> None:
        self._replay_handler = replay_handler

    def send(self, event: dict[str, Any]) -> Any:
        self._replay_args["dataset"] = self._dataset

        self._enrich_event(event_payload=event)

        if not hasattr(self, "_es_index") or self._es_index == "":
            raise ValueError("Elasticsearch index cannot be empty")

        event["_op_type"] = "create"

        if "_index" not in event:
            event["_index"] = self._es_index

        if "_id" not in event:
            # TODO: leaking concrete storage in shipper, refactor to use an abstract callback
            event["_id"] = self._s3_object_id(event)

        self._bulk_actions.append(event)

        if len(self._bulk_actions) < self._bulk_batch_size:
            return

        errors = es_bulk(self._es_client, self._bulk_actions, **self._bulk_kwargs)
        self._handle_outcome(errors=errors)
        self._bulk_actions = []

        return

    def flush(self) -> Any:
        if len(self._bulk_actions) > 0:
            errors = es_bulk(self._es_client, self._bulk_actions, **self._bulk_kwargs)
            self._handle_outcome(errors=errors)

        self._bulk_actions = []

        return

    def discover_dataset(self, event: Dict[str, Any]) -> None:
        if self._dataset == "":
            body: str = event["Records"][0]["body"]
            json_body: Dict[str, Any] = json.loads(body)
            s3_object_key: str = ""

            if "Records" in json_body and len(json_body["Records"]) > 0:
                if "s3" in json_body["Records"][0]:
                    s3_object_key = json_body["Records"][0]["s3"]["object"]["key"]

            if s3_object_key == "":
                shared_logger.warning("s3 object key is empty, dataset set to `generic`")
                self._dataset = "generic"
            else:
                if (
                    "/CloudTrail/" in s3_object_key
                    or "/CloudTrail-Digest/" in s3_object_key
                    or "/CloudTrail-Insight/" in s3_object_key
                ):
                    self._dataset = "aws.cloudtrail"
                elif "exportedlogs" in s3_object_key or "awslogs" in s3_object_key:
                    self._dataset = "aws.cloudwatch_logs"
                elif "/elasticloadbalancing/" in s3_object_key:
                    self._dataset = "aws.elb_logs"
                elif "/network-firewall/" in s3_object_key:
                    self._dataset = "aws.firewall_logs"
                elif "lambda" in s3_object_key:
                    self._dataset = "aws.lambda"
                elif "/SMSUsageReports/" in s3_object_key:
                    self._dataset = "aws.sns"
                elif "/StorageLens/" in s3_object_key:
                    self._dataset = "aws.s3_storage_lens"
                elif "/vpcflowlogs/" in s3_object_key:
                    self._dataset = "aws.vpcflow"
                elif "/WAFLogs/" in s3_object_key:
                    self._dataset = "aws.waf"
                else:
                    self._dataset = "generic"

        shared_logger.debug("dataset", extra={"dataset": self._dataset})

        self._es_index = f"logs-{self._dataset}-{self._namespace}"
