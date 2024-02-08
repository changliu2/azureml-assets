# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Entry script for the LLM-annotation based histogram component.

This file currently contains all the logic for the LLM-annotation based
histogram component.
The reason for putting everything into a single file is that the executors
don't have access to other files by default.
The functionality to simplify this code injection is still being developed.
"""

import argparse
import json
import logging
import os
import re
import tempfile
import time
import traceback
from abc import ABC, abstractmethod
from urllib.request import Request, urlopen

import pandas as pd
import requests
from azure.ai.generative.evaluate import evaluate
from azure.ai.ml.identity import CredentialUnavailableError
from azure.ai.ml.identity._internal import _scopes_to_resource
from azure.core.credentials import AccessToken, TokenCredential
from pyspark.sql import Window
from pyspark.sql.types import IntegerType, StructField, StructType, StringType
from pyspark.sql.functions import col, row_number, monotonically_increasing_id
from shared_utilities import io_utils

_logger = logging.getLogger(__file__)
logging.basicConfig(level=logging.INFO)

TEST_CONNECTION = "test_connection"

RATING = "rating"
INDEX = "index"
PROMPT = "prompt"
COMPLETION = "completion"
CONTEXT = "context"
GROUND_TRUTH = "ground_truth"


# ==================  HTTP Constants ==================
# Timeout per each request: 5min
HTTP_REQUEST_TIMEOUT = 300

# ================= Endpoint Constants =================
AZURE_ENDPOINT_DOMAIN_VALID_PATTERN_RE = r"^(?=.{1,255}$)(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.(?!-)[a-zA-Z0-9-]{1,63}(?<!-))*\.(inference\.ml|openai)\.azure\.com(/openai)?$"  # noqa: E501
AZURE_OPENAI_API_DEPLOYMENT_URL_PATTERN = "https://{}/openai/deployments/{}"

# Parameters to OpenAI API requests
OPENAI_REQUEST_PARAMS = [
    "messages",  # prompt is only a param with the completions API
    "max_tokens",
    "temperature",
    "top_p",
    "n",
    "stream",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "model",
    "num_samples",
]

ENDPOINT_PARAMS = [
    "authorization_header",
    "azure_endpoint_domain_name",
    "azure_openai_api_version",
    "request_error_rate_threshold",
    "api_call_retry_backoff_factor",
    "api_call_retry_max_count",
    "model",
]

THRESHOLD_PARAMS = [
    "groundedness_rating_threshold",
    "similarity_rating_threshold",
    "relevance_rating_threshold",
    "fluency_rating_threshold",
    "coherence_rating_threshold",
]

# ---

CL_100K_BASE = "cl100k_base"
GPT_35_TURBO = "gpt-35-turbo"
GPT_35_TURBO_16K = "gpt-35-turbo-16k"
GPT_4 = "gpt-4"
GPT_4_32K = "gpt-4-32k"

# ---

MIN_RATING = 1
MAX_RATING = 5

GROUNDEDNESS = "Groundedness"
RELEVANCE = "Relevance"
FLUENCY = "Fluency"
COHERENCE = "Coherence"
SIMILARITY = "Similarity"

QAC_METRIC_NAMES = [
    GROUNDEDNESS,
    RELEVANCE,
]
QA_METRIC_NAMES = [
    FLUENCY,
    COHERENCE
]
ALL_METRIC_NAMES = [
    "AcceptableGroundednessScorePerInstance",
    "AggregatedGroundednessPassRate",
    "AcceptableCoherenceScorePerInstance",
    "AggregatedCoherencePassRate",
    "AcceptableFluencyScorePerInstance",
    "AggregatedFluencyPassRate",
    "AcceptableSimilarityScorePerInstance",
    "AggregatedSimilarityPassRate",
    "AcceptableRelevanceScorePerInstance",
    "AggregatedRelevancePassRate",
]
GPT_GROUNDEDNESS = "gpt_groundedness"
GPT_RELEVANCE = "gpt_relevance"
GPT_FLUENCY = "gpt_fluency"
GPT_COHERENCE = "gpt_coherence"
GPT_SIMILARITY = "gpt_similarity"

COMPACT_METRIC_NAME_TO_COLUMN = {
    GROUNDEDNESS: GPT_GROUNDEDNESS,
    RELEVANCE: GPT_RELEVANCE,
    FLUENCY: GPT_FLUENCY,
    COHERENCE: GPT_COHERENCE,
    SIMILARITY: GPT_SIMILARITY
}

COLUMN_TO_COMPACT_METRIC_NAME = {v: k for k, v in COMPACT_METRIC_NAME_TO_COLUMN.items()}

OUTPUT_SPLITTING_REGEX = r"[# ]*Task #*\d+:?"

AUTHORIZATION = "Authorization"
BEARER = "Bearer"
API_KEY = "api-key"
AZURE = "azure"
API_VERSION = "2023-07-01-preview"
METADATA_APIVERSION = "ApiVersion"
METADATA_DEPLOYMENTAPIVERSION = "DeploymentApiVersion"
METADATA_APITYPE = "ApiType"

NUMERICAL = "numerical"
COUNT = "count"
METRIC_NAME = "metric_name"
METRIC_VALUE = "metric_value"
GROUP = "group"
GROUP_DIMENSION = "group_dimension"
SAMPLES_NAME = "samples_name"
ASSET = "asset"
THRESHOLD = "threshold_value"
PRODUCTION_ROW_COUNT = "production_data"
REFERENCE_ROW_COUNT = "reference_data"


def _check_and_format_azure_endpoint_url(
    url_pattern, domain_pattern_re, domain, api_version, model
):
    domain = domain.strip()
    if domain.endswith('/'):
        domain = domain[:-1]

    if not re.match(domain_pattern_re, domain):
        raise RuntimeError(f"Invalid Azure endpoint domain URL: {domain}.")

    url = url_pattern.format(domain, model)

    if api_version:
        url += f"?api-version={api_version}"

    return url


# --- The following is copied from the yet to be released azureml-featurestore.
# TODO: replace with import once it's released.
class AzureMLHoboSparkOnBehalfOfCredential(TokenCredential):
    """Authenticates a user via the on-behalf-of flow on Hobo Spark compute.

    This credential can only be used on
    `Azure Machine Learning Hobo Spark Compute.`
    during job execution when user request to run job during its identity.
    """

    def __init__(self, **kwargs):  # noqa: D107
        provider_type = os.environ.get("AZUREML_DATAPREP_TOKEN_PROVIDER")
        if provider_type != "sparkobo":
            # OBO identity isn't available in this environment
            self._credential = None
        self._credential = _AzureMLHoboSparkOnBehalfOfCredential(**kwargs)

    def get_token(self, *scopes, **kwargs):
        """Request an access token for `scopes`.

        This method is called automatically by Azure SDK clients.

        :param str scopes: desired scope for the access token.
            This credential allows only one scope per request.
        :rtype: azure.core.credentials.AccessToken
        :return: AzureML On behalf of credentials isn't available in the
            hosting environment
        :raises: ~azure.ai.ml.identity.CredentialUnavailableError
        """
        if not self._credential:
            raise CredentialUnavailableError(message=self.get_unavailable_message())

        return self._credential.get_token(*scopes, **kwargs)

    def get_unavailable_message(self) -> str:  # noqa: D102
        return "AzureML On Behalf of credentials not available in this environment."


class _AzureMLHoboSparkOnBehalfOfCredential(object):
    def __init__(self, **kwargs):
        if len(kwargs) > 0:
            env_key_from_kwargs = [
                "AZUREML_SYNAPSE_CLUSTER_IDENTIFIER",
                "AZUREML_SYNAPSE_TOKEN_SERVICE_ENDPOINT",
                "AZUREML_RUN_ID",
                "AZUREML_RUN_TOKEN_EXPIRY",
            ]
            for env_key in env_key_from_kwargs:
                if env_key in kwargs.keys():
                    os.environ[env_key] = kwargs[env_key]
                else:
                    raise Exception(
                        "Unable to initialize AzureMLHoboSparkOBOCredential "
                        "due to invalid arguments"
                    )
        else:
            from pyspark.sql import SparkSession

            try:
                spark = SparkSession.builder.getOrCreate()
            except Exception:  # noqa: B902
                raise Exception(
                    "Fail to get spark session, please check if spark "
                    "environment is set up."
                )

            spark_conf = spark.sparkContext.getConf()
            spark_conf_vars = {
                "AZUREML_SYNAPSE_CLUSTER_IDENTIFIER": "spark.synapse.clusteridentifier",
                "AZUREML_SYNAPSE_TOKEN_SERVICE_ENDPOINT": "spark.tokenServiceEndpoint",
            }
            for env_key, conf_key in spark_conf_vars.items():
                value = spark_conf.get(conf_key)
                if value:
                    os.environ[env_key] = value

        self.obo_service_endpoint = os.environ.get("AZUREML_OBO_SERVICE_ENDPOINT")
        self.token_service_endpoint = os.environ.get(
            "AZUREML_SYNAPSE_TOKEN_SERVICE_ENDPOINT"
        )
        self.obo_access_token = os.environ.get("AZUREML_OBO_CANARY_TOKEN")
        self.cluster_identifier = os.environ.get("AZUREML_SYNAPSE_CLUSTER_IDENTIFIER")
        self.subscription_id = os.environ.get("AZUREML_ARM_SUBSCRIPTION")
        self.resource_group = os.environ.get("AZUREML_ARM_RESOURCEGROUP")
        self.workspace_name = os.environ.get("AZUREML_ARM_WORKSPACE_NAME")
        self.experiment_name = os.environ.get("AZUREML_ARM_PROJECT_NAME")
        self.run_id = os.environ.get("AZUREML_RUN_ID")
        self.oid = os.environ.get("OID")
        self.tid = os.environ.get("TID")

        if not self.obo_access_token:
            return None

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        resource = _scopes_to_resource(*scopes)
        request_url = (
            f"https://{self.token_service_endpoint}/api/v1/proxy/obotoken"
            f"/v1.0/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            "/providers/Microsoft.MachineLearningServices/"
            f"workspaces/{self.workspace_name}/getuseraccesstokenforspark"
        )

        request_body = {
            "oboToken": self.obo_access_token,
            "oid": self.oid,
            "tid": self.tid,
            "resource": resource,
            "experimentName": self.experiment_name,
            "runId": self.run_id,
        }

        headers = {
            "Content-Type": "application/json;charset=utf-8",
            "x-ms-proxy-host": self.obo_service_endpoint,
            "obo-access-token": self.obo_access_token,
            "x-ms-cluster-identifier": self.cluster_identifier,
        }

        print("Attempting to get token from AzureML OBO service.")
        try:
            response = _send_request(request_url, request_body, headers)
            if response:
                response_dict = json.loads(response.read().decode("utf-8"))
                access_token = AccessToken(
                    response_dict["token"], int(time.time()) + 3600
                )
                print("Finished getting token from AzureML OBO service.")
                return access_token
            else:
                print(
                    "Failed to get token from AzureML OBO service. "
                    f"Invalid response: {response.__dict__}"
                )
                return None

        except Exception as e:  # noqa: B902
            print(f"Failing in auth while sending request: {response.__dict__}")
            raise e


def _send_request(url, data=None, headers=None, method=None):
    args = {"url": url}
    if data:
        data = json.dumps(data)
        args["data"] = data.encode("utf8")
    if headers:
        args["headers"] = headers
    if method:
        # the default is GET if data is None, POST otherwise
        args["method"] = method

    try:
        return urlopen(Request(**args), timeout=5)
    except:  # noqa: E722
        raise Exception(f"Failed while sending a request to {url} with data {data}.")


# END of copied code from azureml-featurestore


class _APITokenManager(ABC):
    def __init__(
        self,
        *,
        auth_header,
        **kwargs,
    ):
        self.credential = self.get_aad_credential()
        self.token = None
        self.auth_header = auth_header
        self.last_refresh_time = None

    def get_aad_credential(self):
        return AzureMLHoboSparkOnBehalfOfCredential(
            AZUREML_SYNAPSE_CLUSTER_IDENTIFIER=os.environ[
                "AZUREML_SYNAPSE_CLUSTER_IDENTIFIER"
            ],
            AZUREML_SYNAPSE_TOKEN_SERVICE_ENDPOINT=os.environ[
                "AZUREML_SYNAPSE_TOKEN_SERVICE_ENDPOINT"
            ],
            AZUREML_RUN_ID=os.environ["AZUREML_RUN_ID"],
            AZUREML_RUN_TOKEN_EXPIRY=os.environ["AZUREML_RUN_TOKEN_EXPIRY"],
        )

    @abstractmethod
    def get_token(self):
        pass


class _WorkspaceConnectionTokenManager(_APITokenManager):
    def __init__(
        self,
        *,
        connection_name,
        auth_header,
        **kwargs,
    ):
        super().__init__(auth_header=auth_header)

        try:
            from azureml.dataprep.api._aml_auth._azureml_token_authentication import AzureMLTokenAuthentication
            from azure.ai.ml import MLClient
            from azure.ai.ml.entities import WorkspaceConnection

            self._credential = AzureMLTokenAuthentication._initialize_aml_token_auth()

            uri_match = re.match(r"/subscriptions/(.*)/resourceGroups/(.*)/providers/Microsoft.MachineLearningServices/workspaces/(.*)/connections/(.*)",  # noqa: E501
                                 connection_name, flags=re.IGNORECASE)

            subscription_id = uri_match.group(1)
            resource_group_name = uri_match.group(2)
            workspace_name = uri_match.group(3)
            ml_client = MLClient(
                credential=self._credential,
                subscription_id=subscription_id,
                resource_group_name=resource_group_name,
                workspace_name=workspace_name
            )
            if os.environ.get("AZUREML_RUN_ID", None) is not None:
                # In AzureML Run context, we need to use workspaces internal endpoint that will accept
                # AzureMLToken auth.
                ml_client.connections._operation._client._base_url = f"{os.environ.get('AZUREML_SERVICE_ENDPOINT')}/rp/workspaces"  # noqa: E501
                print(f"Using ml_client base_url: {ml_client.connections._operation._client._base_url}")
                list_secrets_response = ml_client.connections._operation.list_secrets(
                    connection_name=uri_match.group(4),
                    resource_group_name=ml_client.resource_group_name,
                    workspace_name=ml_client.workspace_name,
                )
                connection = WorkspaceConnection._from_rest_object(list_secrets_response)
                print(f"Retrieved Workspace Connection: {connection.id}")

                if connection.type != "azure_open_ai":
                    raise Exception(f"Received unexpected endpoint type {connection.type}"
                                    "only Azure Open AI endpoints are supported at this time")
                api_version = API_VERSION
                if hasattr(connection.metadata, METADATA_APIVERSION):
                    api_version = connection.metadata[METADATA_APIVERSION]
                # this was renamed in latest ml_client
                if hasattr(connection.metadata, METADATA_DEPLOYMENTAPIVERSION):
                    api_version = connection.metadata[METADATA_DEPLOYMENTAPIVERSION]
                # api version
                self.api_version = api_version
                # base_url
                self.domain_name = connection.target
                # api_key
                self.token = connection.credentials["key"]
                self.api_type = None
                if hasattr(connection.metadata, METADATA_APITYPE):
                    self.api_type = connection.metadata[METADATA_APITYPE]
            else:
                raise Exception("Unable to retrieve the token to establish a Workspace Connection")
        except Exception:
            tb = traceback.format_exc()
            raise Exception(f"Error encountered while attempting to authentication token: {tb}")

    def get_api_version(self):
        return self.api_version

    def get_endpoint_domain(self):
        return self.domain_name

    def get_token(self):
        return self.token


def _get_model_type(token_manager, get_model_endpoint):
    try:
        headers = {
            "Content-Type": "application/json",
            "api-key": token_manager.get_token()
        }
        response = requests.get(url=get_model_endpoint, headers=headers, timeout=HTTP_REQUEST_TIMEOUT)
        if response.status_code == 200:
            response_data = response.json()
            model_type = response_data["model"]
        else:
            raise Exception(
                "Received unexpected HTTP status: "
                f"{response.status_code} {response.text}"
            )
    except Exception:
        raise Exception("Error encountered while attempting to get model type")
    return model_type


def get_compact_metric_name(metric_name):
    """Get the compact metric name from the full metric name."""
    return metric_name.replace(" ", "").title()


def run():
    """Compute metrics."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--production_dataset", type=str, required=True)
    parser.add_argument("--metric_names", type=str, required=True)
    parser.add_argument("--model_deployment_name", type=str, required=True)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top_p", type=float, default=1.0)
    parser.add_argument("--num_samples", type=int, default=1)
    parser.add_argument("--frequency_penalty", type=float, default=0.0)
    parser.add_argument("--presence_penalty", type=float, default=0.0)
    parser.add_argument("--stop", type=str, default=None)

    parser.add_argument("--groundedness_rating_threshold", type=int, default=4)
    parser.add_argument("--similarity_rating_threshold", type=int, default=4)
    parser.add_argument("--relevance_rating_threshold", type=int, default=4)
    parser.add_argument("--fluency_rating_threshold", type=int, default=4)
    parser.add_argument("--coherence_rating_threshold", type=int, default=4)

    parser.add_argument("--prompt_column_name", type=str, default=PROMPT)
    parser.add_argument("--completion_column_name", type=str, default=COMPLETION)
    parser.add_argument("--context_column_name", type=str, default=CONTEXT)
    parser.add_argument("--ground_truth_column_name", type=str, default=GROUND_TRUTH)

    parser.add_argument("--sample_rate", type=float, required=False, default=1.0)
    parser.add_argument(
        "--request_error_rate_threshold",
        type=float,
        default=0.5,
        help="Fail if the running error rate for the endpoint requests "
        "raises above this threshold.",
    )
    parser.add_argument("--api_call_retry_backoff_factor", type=int, default=4)
    parser.add_argument("--api_call_retry_max_count", type=int, default=10)
    parser.add_argument("--histogram", type=str, required=True)
    parser.add_argument("--samples_index", type=str, required=True)
    parser.add_argument("--groundedness_violations", type=str, required=True)
    parser.add_argument("--fluency_violations", type=str, required=True)
    parser.add_argument("--relevance_violations", type=str, required=True)
    parser.add_argument("--coherence_violations", type=str, required=True)
    parser.add_argument("--similarity_violations", type=str, required=True)

    parser.add_argument("--workspace_connection_arm_id", type=str, required=True)
    args = parser.parse_args()

    request_args = {
        arg: getattr(args, arg) for arg in OPENAI_REQUEST_PARAMS if hasattr(args, arg)
    }
    endpoint_args = {
        arg: getattr(args, arg) for arg in ENDPOINT_PARAMS if hasattr(args, arg)
    }
    threshold_args = {
        arg: getattr(args, arg) for arg in THRESHOLD_PARAMS if hasattr(args, arg)
    }
    # add model to both request and endpoint args
    # The arg name is longer to be as explicit as possible.
    request_args["model"] = args.model_deployment_name
    endpoint_args["model"] = args.model_deployment_name

    input_metric_names = [m.strip() for m in args.metric_names.split(",")]

    if not (set(input_metric_names) <= set(ALL_METRIC_NAMES)):
        raise ValueError(
            f"metric_names must be a comma-separated list of metric names "
            f"and a subset of {ALL_METRIC_NAMES}, got {args.metric_names}."
        )

    # remove all but groundedness/fluency/coherence/relevance/similarity from metric names and
    # remove duplicates
    pruned_metric_names = [re.sub(r'^(.*?)(Groundedness|Fluency|Coherence|Relevance|Similarity)(.*?)$', r'\2', m) for
                           m in input_metric_names]
    metric_names = list(set(pruned_metric_names))

    # Validate inputs
    if args.temperature < 0.0 or args.temperature > 2.0:
        raise ValueError(f"temperature must be between 0.0 and 2.0, inclusive; "
                         f"got {args.temperature}.")
    if args.top_p < 0.0 or args.top_p > 1.0:
        raise ValueError(f"top_p must be between 0.0 and 1.0, inclusive; got {args.top_p}.")
    if args.num_samples <= 0:
        # TODO support multiple returned annotations
        raise ValueError(f"num_samples must be 1, got {args.num_samples}.")
    if args.frequency_penalty < -2.0 or args.frequency_penalty > 2.0:
        raise ValueError(
            "frequency_penalty must be between -2.0 and 2.0, inclusive; "
            f"got {args.frequency_penalty}."
        )
    if args.presence_penalty < -2.0 or args.presence_penalty > 2.0:
        raise ValueError(
            f"presence_penalty must be between -2.0 and 2.0, inclusive; "
            f"got {args.presence_penalty}."
        )

    if args.sample_rate <= 0.0 or args.sample_rate > 1.0:
        raise ValueError(f"sample_rate must be larger than 0.0 and at most 1.0, "
                         f"got {args.sample_rate}.")

    # TODO add validation for threshold args!!
    print(f"Running with args: {args}")

    violations = {
        "groundedness": args.groundedness_violations,
        "relevance": args.relevance_violations,
        "fluency": args.fluency_violations,
        "similarity": args.similarity_violations,
        "coherence": args.coherence_violations,
    }

    apply_annotation(
        metric_names=metric_names,
        production_dataset=args.production_dataset,
        histogram=args.histogram,
        samples_index=args.samples_index,
        model_deployment_name=args.model_deployment_name,
        workspace_connection_arm_id=args.workspace_connection_arm_id,
        num_samples=args.num_samples,
        sample_rate=args.sample_rate,
        request_args=request_args,
        endpoint_args=endpoint_args,
        threshold_args=threshold_args,
        prompt_column_name=args.prompt_column_name,
        completion_column_name=args.completion_column_name,
        context_column_name=args.context_column_name,
        ground_truth_column_name=args.ground_truth_column_name,
        violations=violations,
    )


def apply_annotation(
    *,
    metric_names,
    production_dataset,
    histogram,
    model_deployment_name,
    workspace_connection_arm_id,
    num_samples,
    sample_rate,
    request_args,
    endpoint_args,
    threshold_args,
    prompt_column_name,
    completion_column_name,
    context_column_name,
    ground_truth_column_name,
    samples_index,
    violations,
):
    """Apply annotation to all samples in the production_dataset."""
    if "chat_history" in [prompt_column_name, completion_column_name, context_column_name, ground_truth_column_name]:
        raise NotImplementedError("chat_history column is not currently supported and cannot be used as specified "
                                  "column. ")

    production_df = io_utils.try_read_mltable_in_spark_with_error(production_dataset, "production_dataset")
    # Ensure input data has the correct columns given the metrics
    # Question, answer required for coherence and fluency
    qa_required = len(list(set(QA_METRIC_NAMES).intersection(
        set(metric_names))))
    for col_name in [prompt_column_name, completion_column_name]:
        if col_name not in production_df.columns and qa_required:
            raise ValueError(f"production_dataset must have column: {col_name}")
    # Question, answer, context required for relevance and groundedness
    qac_required = len(list(set(QAC_METRIC_NAMES).intersection(
        set(metric_names))))
    if qac_required and context_column_name not in production_df.columns:
        raise ValueError(f"production_dataset must have column: {context_column_name}")
    # Question, answer, ground-truth required for similarity
    if SIMILARITY in metric_names and ground_truth_column_name not in production_df.columns:
        raise ValueError(f"production_dataset must have column: {ground_truth_column_name}")

    column_names = [prompt_column_name, completion_column_name, context_column_name, ground_truth_column_name]
    if len(column_names) != len(set(column_names)):
        raise ValueError("Detected duplicate specified columns. Column name input cannot be the same. Please ensure "
                         f"that the column input specified is unique.\nReceived prompt_column_name: "
                         f"{prompt_column_name}\ncompletion_column_name: {completion_column_name}\n"
                         f"context_column_name: {context_column_name}\nground_truth_column_name: "
                         f"{ground_truth_column_name}")

    # rename columns to prompt, completion, context, ground truth to match metaprompt data
    production_df = (production_df.withColumnRenamed(prompt_column_name, PROMPT)
                     .withColumnRenamed(completion_column_name, COMPLETION)
                     .withColumnRenamed(context_column_name, CONTEXT)
                     .withColumnRenamed(ground_truth_column_name, GROUND_TRUTH))
    # Sampling
    production_df_sampled = production_df.sample(withReplacement=False, fraction=sample_rate)
    if production_df_sampled.count() == 0:
        print("Not enough data resulting from sample_rate and production dataset. "
              "Using first five rows of production dataset instead. To use custom sample_rate with this dataset, "
              "try increasing sample_rate value.")
        # Default to 5
        production_df_sampled = production_df.limit(5)

    production_df = production_df_sampled
    row_count = production_df.count()
    production_df_with_index = production_df_sampled.withColumn("id", row_number()
                                                                .over(Window.orderBy(monotonically_increasing_id()))-1)

    spark = io_utils.init_spark()
    spark_conf = spark.sparkContext.getConf()
    spark_conf_vars = {
        "AZUREML_SYNAPSE_CLUSTER_IDENTIFIER": "spark.synapse.clusteridentifier",  # noqa: E501
        "AZUREML_SYNAPSE_TOKEN_SERVICE_ENDPOINT": "spark.tokenServiceEndpoint",
    }
    for env_key, conf_key in spark_conf_vars.items():
        value = spark_conf.get(conf_key)
        if value:
            os.environ[env_key] = value

    driver_env_vars = {
        k: v
        for k, v in os.environ.items()
        if k
        in [
            "AZUREML_SYNAPSE_CLUSTER_IDENTIFIER",
            "AZUREML_SYNAPSE_TOKEN_SERVICE_ENDPOINT",
            "AZUREML_RUN_ID",
            "AZUREML_RUN_TOKEN_EXPIRY",
            "AZUREML_OBO_SERVICE_ENDPOINT",
            "AZUREML_OBO_CANARY_TOKEN",
            "AZUREML_ARM_SUBSCRIPTION",
            "AZUREML_ARM_RESOURCEGROUP",
            "AZUREML_ARM_WORKSPACE_NAME",
            "AZUREML_ARM_PROJECT_NAME",
            "OID",
            "TID",
        ]
    }
    is_test_connection = False
    if workspace_connection_arm_id == TEST_CONNECTION:
        # Used for testing component e2e without consuming OpenAI endpoint
        endpoint_domain_name = TEST_CONNECTION
        api_version = API_VERSION
        is_test_connection = True
        token_manager = None
        model_type = GPT_4
    else:
        try:
            # Define authorization token manager
            token_manager_class = _WorkspaceConnectionTokenManager
            token_manager = token_manager_class(
                connection_name=workspace_connection_arm_id,
                auth_header=API_KEY
            )
        except Exception as e:
            print(f"Unable to process request: {e}")
            return

        endpoint_domain_name = token_manager.get_endpoint_domain().replace("https://", "")
        api_version = token_manager.get_api_version()
        api_key = token_manager.get_token()
        api_base = token_manager.get_endpoint_domain()

        print(
            "Created token manager for auth type "
            f"managed identity using auth header {API_KEY}."
        )
        # use fixed API version since newer versions aren't supported
        get_model_endpoint = _check_and_format_azure_endpoint_url(
            AZURE_OPENAI_API_DEPLOYMENT_URL_PATTERN,
            AZURE_ENDPOINT_DOMAIN_VALID_PATTERN_RE,
            endpoint_domain_name, "2022-12-01",
            model_deployment_name)
        model_type = _get_model_type(token_manager, get_model_endpoint)

    all_metrics_pdf = None
    samples_index_rows = []
    metrics_list = []
    for metric_name in metric_names:
        metric_name_compact = get_compact_metric_name(metric_name)
        column_name = COMPACT_METRIC_NAME_TO_COLUMN[metric_name_compact]
        metrics_list.append(column_name)
    has_context = CONTEXT in production_df.columns
    has_ground_truth = GROUND_TRUTH in production_df.columns

    def annotate_batch(iterator):
        for batch in iterator:
            # add environment variables on executors
            for env_var_key, env_var_value in driver_env_vars.items():
                os.environ[env_var_key] = env_var_value

            rows = []
            for index, row in batch.iterrows():
                qca = {PROMPT: row[PROMPT], COMPLETION: row[COMPLETION]}
                if has_context:
                    qca[CONTEXT] = row[CONTEXT]
                if has_ground_truth:
                    qca[GROUND_TRUTH] = row[GROUND_TRUTH]
                rows.append(qca)

            output_dir = tempfile.TemporaryDirectory()
            evaluate(
                evaluation_name="gsq-evaluation",
                data=rows,
                task_type="qa",
                data_mapping={
                    "questions": PROMPT,
                    "contexts": CONTEXT,
                    "y_pred": COMPLETION,
                    "y_test": GROUND_TRUTH
                },
                model_config={
                    "api_version": api_version,
                    "api_base": api_base,
                    "api_type": AZURE,
                    "api_key": api_key,
                    "deployment_id": model_type
                },
                metrics_list=metrics_list,
                output_path=output_dir.name
            )
            tabular_result = pd.read_json(os.path.join(output_dir.name, "eval_results.jsonl"), lines=True)
            # add index column
            tabular_result = tabular_result.reset_index(names=INDEX)
            # rename metric columns
            for column_name in metrics_list:
                tabular_result.rename(
                    columns={column_name: COLUMN_TO_COMPACT_METRIC_NAME[column_name]},
                    inplace=True)
            yield tabular_result

    # used for testing without using openai connection
    def mock_metrics_batch(iterator):
        for batch in iterator:
            rows = []
            for index, row in batch.iterrows():
                qca = {PROMPT: row[PROMPT], COMPLETION: row[COMPLETION]}
                if has_context:
                    qca[CONTEXT] = row[CONTEXT]
                if has_ground_truth:
                    qca[GROUND_TRUTH] = row[GROUND_TRUTH]
                for metric_name in metric_names:
                    metric_name_compact = get_compact_metric_name(metric_name)
                    qca[metric_name_compact] = 1
                rows.append(qca)
            tabular_result = pd.DataFrame(rows)
            # add index column
            tabular_result = tabular_result.reset_index(names=INDEX)
            yield tabular_result

    schema_fields = [
        StructField(INDEX, IntegerType(), True),
        StructField(PROMPT, StringType(), True)
    ]
    if has_context:
        schema_fields.append(StructField(CONTEXT, StringType(), True))
    schema_fields.append(StructField(COMPLETION, StringType(), True))
    if has_ground_truth:
        schema_fields.append(StructField(GROUND_TRUTH, StringType(), True))
    for metric_name in metric_names:
        metric_name_compact = get_compact_metric_name(metric_name)
        schema_fields.append(StructField(metric_name_compact, IntegerType(), True))
    schema = StructType(schema_fields)
    if is_test_connection:
        annotations_df = production_df.mapInPandas(
            mock_metrics_batch,
            schema=schema,
        ).cache()
    else:
        annotations_df = production_df.mapInPandas(
            annotate_batch,
            schema=schema,
        ).cache()

    print("showing annotations dataframe: ")
    annotations_df.show()

    for metric_name in metric_names:
        # Run inference over input dataset
        print(f"Begin {metric_name} processing.")
        metric_name_compact = get_compact_metric_name(metric_name)
        # Get rating counts
        metrics_df = annotations_df.select(metric_name_compact).groupBy(metric_name_compact).count()
        metrics_df.show()
        print("Finished annotating answers.")
        metrics_pdf = metrics_df.withColumnRenamed(metric_name_compact, RATING).select("*").toPandas()
        print(metrics_pdf)
        ratings = metrics_pdf.rating.to_list()
        missing_ratings = set(range(MIN_RATING, MAX_RATING + 1)) - set(ratings)
        for r in missing_ratings:
            metrics_pdf.loc[len(metrics_pdf)] = {RATING: r, COUNT: 0}
        metrics_pdf[RATING] = metrics_pdf[RATING].map(lambda r: str(r))
        # add metric_name, metric_value, group, and threshold values
        metrics_pdf.rename(columns={RATING: GROUP, COUNT: METRIC_VALUE, }, inplace=True)
        metrics_pdf[METRIC_NAME] = f"Acceptable{metric_name_compact}ScorePerInstance"
        metric_threshold_value = str(threshold_args[f"{metric_name_compact.lower()}_rating_threshold"])
        metrics_pdf[THRESHOLD] = metric_threshold_value
        print(metrics_pdf)

        # create violations table if there are violations
        violations_df = annotations_df.select(
            [metric_name_compact, INDEX]).filter(
                (col(metric_name_compact) < metric_threshold_value) & (col(INDEX) != -1))
        if violations_df.count() > 0:
            violations_df_full = production_df_with_index.join(violations_df,
                                                               production_df_with_index.id == violations_df.index,
                                                               "inner").drop(violations_df.index).drop(
                                                                   production_df_with_index.id).withColumnRenamed(
                                                                       'rating', metric_name_compact)
            run_id = os.environ.get("AZUREML_RUN_ID")
            io_utils.save_spark_df_as_mltable(violations_df_full, violations[metric_name_compact.lower()])
            samples_index_rows.append({METRIC_NAME: f"Acceptable{metric_name_compact}ScorePerInstance",
                                       GROUP: "",
                                       GROUP_DIMENSION: "",
                                       SAMPLES_NAME: "Violations",
                                       ASSET: f"azureml_{run_id}_output_data_{metric_name_compact.lower()}_violations:1"})  # noqa: E501

        if all_metrics_pdf is None:
            all_metrics_pdf = metrics_pdf
        else:
            all_metrics_pdf = pd.concat([all_metrics_pdf, metrics_pdf])

    print(f"Adding {PRODUCTION_ROW_COUNT} and {REFERENCE_ROW_COUNT}.")
    for row_count_name in [PRODUCTION_ROW_COUNT, REFERENCE_ROW_COUNT]:
        row = {
            METRIC_NAME: "RowCount",
            METRIC_VALUE: row_count,
            GROUP: row_count_name,
            THRESHOLD: ""
        }
        all_metrics_pdf.loc[len(all_metrics_pdf)] = row
    print("Finished calculating metrics based on annotations.")

    metadata_schema = StructType(
        [
            StructField(METRIC_NAME, StringType(), True),
            StructField(GROUP, StringType(), True),
            StructField(GROUP_DIMENSION, StringType(), True),
            StructField(SAMPLES_NAME, StringType(), True),
            StructField(ASSET, StringType(), True),
        ]
    )
    # Create a new DataFrame for the samples index data
    samples_df = spark.createDataFrame(samples_index_rows, metadata_schema)
    io_utils.save_spark_df_as_mltable(samples_df, samples_index)

    # temporary workaround for pandas>2.0 until pyspark upgraded to 3.4.1, see issue:
    # https://stackoverflow.com/questions/76404811/attributeerror-dataframe-object-has-no-attribute-iteritems
    pd.DataFrame.iteritems = pd.DataFrame.items
    io_utils.save_spark_df_as_mltable(
        spark.createDataFrame(all_metrics_pdf),
        histogram)


if __name__ == "__main__":
    run()