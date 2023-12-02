# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Class for online endpoint model."""


from typing import Optional
from azureml.core import Run, Model
from aml_benchmark.utils.exceptions import BenchmarkUserException
from aml_benchmark.utils.error_definitions import BenchmarkUserError
from azureml._common._error_definition.azureml_error import AzureMLError

from ..logging import get_logger
from ..aml_run_utils import get_dependent_run


logger = get_logger(__name__)


class OnlineEndpointModel:
    """Class for online endpoint model."""
    AOAI_ENDPOINT_URL_BASE = [
        'openai.azure.com', 'api.cognitive.microsoft.com', 'cognitiveservices.azure.com/']

    def __init__(
            self, model: str, model_version: Optional[str], model_type: str,
            endpoint_url: Optional[str] = None,
            is_finetuned: Optional[bool] = False,
            finetuned_subscription_id: Optional[str] = None,
            finetuned_resource_group: Optional[str] = None,
            finetuned_workspace: Optional[str] = None,
            model_depend_step: Optional[str] = None
    ):
        """Init method."""
        if model is not None and model.startswith('azureml:'):
            self._model_name = model.split('/')[-3]
            self._model_path = model
            if model_version is None:
                self._model_version = model.split('/')[-1]
        else:
            self._model_name = model
            self._model_path = None
            self._model_version = model_version
        self._model_type = model_type
        if model_type is None:
            self._model_type = self._get_model_type_from_url(endpoint_url)
        self._is_finetuned = is_finetuned
        self._finetuned_subscription_id = finetuned_subscription_id
        self._finetuned_resource_group = finetuned_resource_group
        self._finetuned_workspace = finetuned_workspace
        self._model_depend_step = model_depend_step

    @property
    def model_name(self) -> str:
        """Get the model name."""
        return self._model_name

    @property
    def model_version(self) -> str:
        """Get the model version."""
        if self._model_version is None and self.is_finetuned:
            logger.warning('Model version is None. Trying to get the latest one.')
            finetuned_run_id = None
            if self._model_depend_step is not None:
                finetuned_run = get_dependent_run(self._model_depend_step)
                ws = Run.get_context().experiment.workspace
                finetuned_run_id = finetuned_run.id
            models = list(Model.list(ws, self._model_name, run_id=finetuned_run_id))
            if len(models) == 0:
                raise BenchmarkUserException._with_error(
                    AzureMLError.create(
                        BenchmarkUserError,
                        error_details=f"No associate version with model name {self._model_name} "
                                      f"in step {self._model_depend_step} can be found. Please make sure the finetuned step"
                                      f" {self._model_depend_step} has successfully registered the model."
                    )
                )
            self._model_version = str(models[0].version)
            if len(models) > 1:
                logger.warning(
                    f"Multiple models with name {self._model_name} are found. Use first one with version  {self._model_version} now.")
        return self._model_version

    @property
    def model_type(self) -> str:
        """Get the model type."""
        return self._model_type

    @property
    def is_finetuned(self) -> bool:
        """Get the finetune flag."""
        return self._is_finetuned

    @property
    def source(self) -> Optional[str]:
        """Get the source."""
        if self.is_finetuned:
            source_list = [
                "/subscriptions", self._finetuned_subscription_id, "resourceGroups",
                self._finetuned_resource_group, "providers", "Microsoft.MachineLearningServices",
                "workspaces", self._finetuned_workspace
            ]
            return "/".join(source_list)
        return None

    @property
    def model_path(self) -> str:
        """Get the model path."""
        if self._model_path is None and self.is_oss_model():
            self._model_path = 'azureml://registries/azureml-meta/models/{}/versions/{}'.format(
                self._model_name,
                self._model_version
            )
        return self._model_path

    def is_aoai_model(self) -> bool:
        """Check if the model is aoai model."""
        return self._model_type == 'oai'

    def is_oss_model(self) -> bool:
        """Check if the model is llama model."""
        return self._model_type == 'oss'

    def is_vision_oss_model(self) -> bool:
        """Check if the model is a vision oss model."""
        return self._model_type == "vision_oss"

    def _get_model_type_from_url(self, endpoint_url: str) -> str:
        if endpoint_url is None:
            logger.warning('Endpoint url is None. Default to oss.')
            return 'oss'
        for base_url in OnlineEndpointModel.AOAI_ENDPOINT_URL_BASE:
            if base_url in endpoint_url:
                return 'oai'
        return 'oss'
