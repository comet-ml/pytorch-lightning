# Copyright The Lightning AI team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Comet Logger
------------
"""

import logging
import os
from argparse import Namespace
from typing import Any, Dict, Literal, Mapping, Optional, TYPE_CHECKING, Union

from lightning_utilities.core.imports import RequirementCache
from torch import Tensor
from torch.nn import Module
from typing_extensions import override

from lightning.fabric.utilities.logger import _convert_params
from lightning.pytorch.loggers.logger import Logger, rank_zero_experiment
from lightning.pytorch.utilities.rank_zero import rank_zero_only

if TYPE_CHECKING:
    from comet_ml import ExistingExperiment, Experiment, OfflineExperiment, ExperimentConfig, BaseExperiment

log = logging.getLogger(__name__)
_COMET_AVAILABLE = RequirementCache("comet-ml>=3.44.4", module="comet_ml")

comet_experiment = Union["Experiment", "ExistingExperiment", "OfflineExperiment"]
framework = "pytorch-lightning"


class CometLogger(Logger):
    r"""Track your parameters, metrics, source code and more using `Comet
    <https://www.comet.com/?utm_source=lightning.pytorch&utm_medium=referral>`_.

    Install it with pip:

    .. code-block:: bash

        pip install comet-ml

    Comet requires either an API Key (online mode) or a local directory path (offline mode).

    **ONLINE MODE**

    .. code-block:: python

        import os
        from lightning.pytorch import Trainer
        from lightning.pytorch.loggers import CometLogger

        # arguments made to CometLogger are passed on to the comet_ml.Experiment class
        comet_logger = CometLogger(
            api_key=os.environ.get("COMET_API_KEY"),
            workspace=os.environ.get("COMET_WORKSPACE"),  # Optional
            save_dir=".",  # Optional
            project_name="default_project",  # Optional
            experiment_key=os.environ.get("COMET_EXPERIMENT_KEY"),  # Optional
            experiment_name="lightning_logs",  # Optional
        )
        trainer = Trainer(logger=comet_logger)

    **OFFLINE MODE**

    .. code-block:: python

        from lightning.pytorch.loggers import CometLogger

        # arguments made to CometLogger are passed on to the comet_ml.Experiment class
        comet_logger = CometLogger(
            save_dir=".",
            workspace=os.environ.get("COMET_WORKSPACE"),  # Optional
            project_name="default_project",  # Optional
            experiment_name="lightning_logs",  # Optional
        )
        trainer = Trainer(logger=comet_logger)

    **Log Hyperparameters:**

    Log parameters used to initialize a :class:`~lightning.pytorch.core.LightningModule`:

    .. code-block:: python

        class LitModule(LightningModule):
            def __init__(self, *args, **kwarg):
                self.save_hyperparameters()

    Log other Experiment Parameters

    .. code-block:: python

        # log a single parameter
        logger.log_hyperparams({"batch_size": 16})

        # log multiple parameters
        logger.log_hyperparams({"batch_size": 16, "learning_rate": 0.001})

        # log nested parameters
        logger.log_hyperparams({"specific": {'param': {'subparam': "value"}}})

    **Log Metrics:**

    .. code-block:: python

        # log a single metric
        logger.log_metrics({"train/loss": 0.001})

        # add multiple metrics
        logger.log_metrics({"train/loss": 0.001, "val/loss": 0.002})

        # add nested metrics
        logger.log_hyperparams({"specific": {'metric': {'submetric': "value"}}})

    **Access the Comet Experiment object:**

    You can gain access to the underlying Comet
    `Experiment <https://www.comet.com/docs/v2/api-and-sdk/python-sdk/reference/Experiment/>`__ object
    and its methods through the :obj:`logger.experiment` property. This will let you use
    the additional logging features provided by the Comet SDK.

    Some examples of data you can log through the Experiment object:

    Log Image data:

    .. code-block:: python

        img = PIL.Image.open("<path to image>")
        logger.experiment.log_image(img, file_name="my_image.png")

    Log Text data:

    .. code-block:: python

        text = "Lightning is awesome!"
        logger.experiment.log_text(text)

    Log Audio data:

    .. code-block:: python

        audio = "<path to audio data>"
        logger.experiment.log_audio(audio, file_name="my_audio.wav")

    Log arbitrary data assets:

    You can log any type of data to Comet as an asset. These can be model
    checkpoints, datasets, debug logs, etc.

    .. code-block:: python

        logger.experiment.log_asset("<path to your asset>", file_name="my_data.pkl")

    Log Models to Comet's Model Registry:

    .. code-block:: python

        logger.experiment.log_model(name="my-model", "<path to your model>")

    See Also:
        - `Demo in Google Colab <https://tinyurl.com/22phzw5s>`__
        - `Comet Documentation <https://www.comet.com/docs/v2/integrations/ml-frameworks/pytorch-lightning/>`__

    Args:
        api_key (str, optional): Comet API key. It's recommended to configure the API Key with `comet login`.
        workspace (str, optional): Comet workspace name. If not provided, uses the default workspace.
        project (str, optional): Comet project name. Defaults to `Uncategorized`.
        experiment_key (str, optional): The Experiment identifier to be used for logging. This is used either to append
            data to an Existing Experiment or to control the key of new experiments (for example to match another
            identifier). Must be an alphanumeric string whose length is between 32 and 50 characters.
        mode (str, optional): Control how the Comet experiment is started.
            * ``"get_or_create"``: Starts a fresh experiment if required, or persists logging to an existing one.
            * ``"get"``: Continue logging to an existing experiment identified by the ``experiment_key`` value.
            * ``"create"``: Always creates of a new experiment, useful for HPO sweeps.
        online (boolean, optional): If True, the data will be logged to Comet server, otherwise it will be stored
            locally in an offline experiment. Default is ``True``.
        **kwargs: Additional arguments like `experiment_name`, `log_code`, `prefix`, `offline_directory` etc. used by
            :class:`CometExperiment` can be passed as keyword arguments in this logger.

    Raises:
        ModuleNotFoundError:
            If required Comet package is not installed on the device.
        ValueError: If no API Key is set in online mode.
        ExperimentNotFound: If mode="get" and the experiment_key doesn't exist, or you don't have access to it.
        InvalidExperimentMode:
            * If mode="get" but no experiment_key was passed or configured.
            * If mode="create", an experiment_key was passed or configured and
              an Experiment with that Key already exists.

    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        workspace: Optional[str] = None,
        project: Optional[str] = None,
        experiment_key: Optional[str] = None,
        mode: Optional[Literal["get_or_create", "get", "create"]] = None,
        online: Optional[bool] = None,
        **kwargs: Any,
    ):
        if not _COMET_AVAILABLE:
            raise ModuleNotFoundError(str(_COMET_AVAILABLE))

        super().__init__()

        # needs to be set before the first `comet_ml` import
        # because comet_ml imported after another machine learning libraries (Torch)
        os.environ["COMET_DISABLE_AUTO_LOGGING"] = "1"

        self._prefix = kwargs.pop("prefix", None)

        import comet_ml

        comet_config = comet_ml.ExperimentConfig(**kwargs)

        self._experiment = comet_ml.start(
            api_key=api_key,
            workspace=workspace,
            project=project,
            experiment_key=experiment_key,
            mode=mode,
            online=online,
            experiment_config=comet_config,
        )

        self._experiment.log_other("Created from", "pytorch-lightning")

    @property
    @rank_zero_experiment
    def experiment(self) -> Union["Experiment", "ExistingExperiment", "OfflineExperiment", "BaseExperiment"]:
        r"""Actual Comet object. To use Comet features in your :class:`~lightning.pytorch.core.LightningModule` do the
        following.

        Example::

            self.logger.experiment.some_comet_function()

        """
        return self._experiment

    @override
    @rank_zero_only
    def log_hyperparams(self, params: Union[Dict[str, Any], Namespace]) -> None:
        params = _convert_params(params)
        self.experiment.__internal_api__log_parameters__(
            parameters=params,
            framework=framework,
        )

    @override
    @rank_zero_only
    def log_metrics(self, metrics: Mapping[str, Union[Tensor, float]], step: Optional[int] = None) -> None:
        assert rank_zero_only.rank == 0, "experiment tried to log from global_rank != 0"
        # Comet.com expects metrics to be a dictionary of detached tensors on CPU
        metrics_without_epoch = metrics.copy()
        for key, val in metrics_without_epoch.items():
            if isinstance(val, Tensor):
                metrics_without_epoch[key] = val.cpu().detach()

        epoch = metrics_without_epoch.pop("epoch", None)
        self.experiment.__internal_api__log_metrics__(
            metrics_without_epoch,
            step=step,
            epoch=epoch,
            prefix=self._prefix,
            framework=framework,
        )

    @override
    @rank_zero_only
    def finalize(self, status: str) -> None:
        """We will not end experiment (self._experiment.end()) here to have an ability to continue using it after
        training is complete but instead of ending we will upload/save all the data."""
        if self._experiment is None:
            # When using multiprocessing, finalize() should be a no-op on the main process, as no experiment has been
            # initialized there
            return

        # just save the data
        self.experiment.flush()

    @property
    @override
    def save_dir(self) -> Optional[str]:
        """Gets the save directory.

        Returns:
            The path to the save directory.

        """
        import comet_ml
        if isinstance(self._experiment, comet_ml.OfflineExperiment):
            return self._experiment.offline_directory

        return None

    @property
    @override
    def name(self) -> str:
        """Gets the project name.

        Returns:
            The project name.

        """
        return self._experiment.project_name

    @property
    @override
    def version(self) -> str:
        """Gets the version.

        Returns:
            experiment key

        """
        return self._experiment.get_key()

    def __getstate__(self) -> Dict[str, Any]:
        state = self.__dict__.copy()

        # Save the experiment id in case an experiment object already exists,
        # this way we could create an ExistingExperiment pointing to the same
        # experiment
        state["_experiment_key"] = self._experiment.get_key() if self._experiment is not None else None

        # Remove the experiment object as it contains hard to pickle objects
        # (like network connections), the experiment object will be recreated if
        # needed later
        state["_experiment"] = None
        return state

    @override
    def log_graph(self, model: Module, input_array: Optional[Tensor] = None) -> None:
        self._experiment.__internal_api__set_model_graph__(
            graph=model,
            framework=framework,
        )
