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

from lightning.fabric.utilities.logger import _add_prefix, _convert_params
from lightning.pytorch.loggers.logger import Logger, rank_zero_experiment
from lightning.pytorch.utilities.exceptions import MisconfigurationException
from lightning.pytorch.utilities.rank_zero import rank_zero_only

if TYPE_CHECKING:
    from comet_ml import ExistingExperiment, Experiment, OfflineExperiment

log = logging.getLogger(__name__)
_COMET_AVAILABLE = RequirementCache("comet-ml>=3.44.4", module="comet_ml")


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

    **Log Metrics:**

    .. code-block:: python

        # log a single metric
        logger.log_metrics({"train/loss": 0.001})

        # add multiple metrics
        logger.log_metrics({"train/loss": 0.001, "val/loss": 0.002})

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
        api_key: Required in online mode. API key, found on Comet.ml. If not given, this
            will be loaded from the environment variable COMET_API_KEY or ~/.comet.config
            if either exists.
        save_dir: Required in offline mode. The path for the directory to save local
            comet logs. If given, this also sets the directory for saving checkpoints.
        project_name: Optional. Send your experiment to a specific project.
            Otherwise, will be sent to Uncategorized Experiments.
            If the project name does not already exist, Comet.ml will create a new project.
        experiment_name: Optional. String representing the name for this particular experiment on Comet.ml.
        experiment_key: Optional. If set, restores from existing experiment.
        offline: If api_key and save_dir are both given, this determines whether
            the experiment will be in online or offline mode. This is useful if you use
            save_dir to control the checkpoints directory and have a ~/.comet.config
            file but still want to run offline experiments.
        prefix: A string to put at the beginning of metric keys.
        **kwargs: Additional arguments like `workspace`, `log_code`, etc. used by
            :class:`CometExperiment` can be passed as keyword arguments in this logger.

    Raises:
        ModuleNotFoundError:
            If required Comet package is not installed on the device.
        MisconfigurationException:
            If neither ``api_key`` nor ``save_dir`` are passed as arguments.

    """

    LOGGER_JOIN_CHAR = "-"

    def __init__(
        self,
        api_key: Optional[str] = None,
        save_dir: Optional[str] = None,
        project_name: Optional[str] = None,
        experiment_name: Optional[str] = None,
        experiment_key: Optional[str] = None,
        offline: bool = False,
        prefix: str = "",
        **kwargs: Any,
    ):
        if not _COMET_AVAILABLE:
            raise ModuleNotFoundError(str(_COMET_AVAILABLE))

        self._save_dir: Optional[str]
        self.api_key: str
        self.mode: Literal["online", "offline"]

        super().__init__()

        # needs to be set before the first `comet_ml` import
        # because comet_ml imported after another machine learning libraries (Torch)
        os.environ["COMET_DISABLE_AUTO_LOGGING"] = "1"

        import comet_ml

        comet_experiment = Union[comet_ml.Experiment, comet_ml.ExistingExperiment, comet_ml.OfflineExperiment]
        self._experiment: Optional[comet_experiment] = None

        # Determine online or offline mode based on which arguments were passed to CometLogger
        api_key = api_key or comet_ml.config.get_api_key(None, comet_ml.config.get_config())

        if api_key is not None and save_dir is not None:
            self.mode = "offline" if offline else "online"
            self.api_key = api_key
            self._save_dir = save_dir
        elif api_key is not None:
            self.mode = "online"
            self.api_key = api_key
            self._save_dir = None
        elif save_dir is not None:
            self.mode = "offline"
            self._save_dir = save_dir
        else:
            # If neither api_key nor save_dir are passed as arguments, raise an exception
            raise MisconfigurationException("CometLogger requires either api_key or save_dir during initialization.")

        log.info(f"CometLogger will be initialized in {self.mode} mode")

        self._project_name: Optional[str] = project_name
        self._experiment_key: str = experiment_key or os.environ.get("COMET_EXPERIMENT_KEY") or comet_ml.generate_guid()
        self._experiment_name: Optional[str] = experiment_name
        self._prefix: str = prefix
        self._kwargs: Dict[str, Any] = kwargs

    @property
    @rank_zero_experiment
    def experiment(self) -> Union["Experiment", "ExistingExperiment", "OfflineExperiment"]:
        r"""Actual Comet object. To use Comet features in your :class:`~lightning.pytorch.core.LightningModule` do the
        following.

        Example::

            self.logger.experiment.some_comet_function()

        """
        if self._experiment is not None and self._experiment.alive:
            return self._experiment

        import comet_ml

        comet_comfig = comet_ml.ExperimentConfig(
            offline_directory=self._save_dir,
            name=self._experiment_name,
            **self._kwargs,
        )

        self._experiment = comet_ml.start(
            api_key=self.api_key,
            project=self._project_name,
            experiment_key=self._experiment_key,
            online=self.mode == "online",
            experiment_config=comet_comfig,
        )

        self._experiment.log_other("Created from", "pytorch-lightning")

        return self._experiment

    @override
    @rank_zero_only
    def log_hyperparams(self, params: Union[Dict[str, Any], Namespace]) -> None:
        params = _convert_params(params)
        self.experiment.log_parameters(params)

    @override
    @rank_zero_only
    def log_metrics(self, metrics: Mapping[str, Union[Tensor, float]], step: Optional[int] = None) -> None:
        assert rank_zero_only.rank == 0, "experiment tried to log from global_rank != 0"
        # Comet.ml expects metrics to be a dictionary of detached tensors on CPU
        metrics_without_epoch = metrics.copy()
        for key, val in metrics_without_epoch.items():
            if isinstance(val, Tensor):
                metrics_without_epoch[key] = val.cpu().detach()

        epoch = metrics_without_epoch.pop("epoch", None)
        metrics_without_epoch = _add_prefix(metrics_without_epoch, self._prefix, self.LOGGER_JOIN_CHAR)
        self.experiment.log_metrics(metrics_without_epoch, step=step, epoch=epoch)

    def reset_experiment(self) -> None:
        self._experiment = None

    @override
    @rank_zero_only
    def finalize(self, status: str) -> None:
        r"""When calling ``self.experiment.end()``, that experiment won't log any more data to Comet. That's why, if you
        need to log any more data, you need to create an ExistingCometExperiment. For example, to log data when testing
        your model after training, because when training is finalized :meth:`CometLogger.finalize` is called.

        This happens automatically in the :meth:`~CometLogger.experiment` property, when
        ``self._experiment`` is set to ``None``, i.e. ``self.reset_experiment()``.

        """
        if self._experiment is None:
            # When using multiprocessing, finalize() should be a no-op on the main process, as no experiment has been
            # initialized there
            return
        self.experiment.end()
        self.reset_experiment()

    @property
    @override
    def save_dir(self) -> Optional[str]:
        """Gets the save directory.

        Returns:
            The path to the save directory.

        """
        return self._save_dir

    @property
    @override
    def name(self) -> str:
        """Gets the project name.

        Returns:
            The project name if it is specified, else "comet-default".

        """
        # Don't create an experiment if we don't have one
        if self._experiment is not None and self._experiment.project_name is not None:
            return self._experiment.project_name

        if self._project_name is not None:
            return self._project_name

        return "comet-default"

    @property
    @override
    def version(self) -> str:
        """Gets the version.

        Returns:
            experiment id/key
        """
        if self._experiment is not None:
            return self._experiment.id

        return self._experiment_key

    def __getstate__(self) -> Dict[str, Any]:
        state = self.__dict__.copy()

        # Save the experiment id in case an experiment object already exists,
        # this way we could create an ExistingExperiment pointing to the same
        # experiment
        state["_experiment_key"] = self._experiment.id if self._experiment is not None else None

        # Remove the experiment object as it contains hard to pickle objects
        # (like network connections), the experiment object will be recreated if
        # needed later
        state["_experiment"] = None
        return state

    @override
    def log_graph(self, model: Module, input_array: Optional[Tensor] = None) -> None:
        if self._experiment is not None:
            self._experiment.set_model_graph(model)
