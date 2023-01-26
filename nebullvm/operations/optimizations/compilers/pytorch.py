from typing import List, Tuple, Union

from nebullvm.config import QUANTIZATION_DATA_NUM
from nebullvm.operations.optimizations.compilers.base import Compiler
from nebullvm.operations.optimizations.compilers.quantizations.pytorch import (
    quantize_pytorch,
)
from nebullvm.operations.optimizations.compilers.quantizations.utils import (
    check_quantization,
)
from nebullvm.optional_modules.torch import (
    GraphModule,
    Module,
    ScriptModule,
    symbolic_trace,
    torch,
)
from nebullvm.tools.base import Device, QuantizationType
from nebullvm.tools.data import DataManager
from nebullvm.tools.transformations import MultiStageTransformation


class PytorchBackendCompiler(Compiler):
    supported_ops = {
        "cpu": [None, QuantizationType.STATIC, QuantizationType.DYNAMIC],
        "gpu": [
            None,
            QuantizationType.HALF,
        ],
    }

    def execute(
        self,
        model: Module,
        input_tfms: MultiStageTransformation = None,
        metric_drop_ths: float = None,
        quantization_type: QuantizationType = None,
        input_data: DataManager = None,
        **kwargs,
    ):
        """Optimize the input model using pytorch built-in techniques.

        Args:
            model (torch.nn.Module): The pytorch model.
            input_tfms (MultiStageTransformation, optional): Transformations
                to be performed to the model's input tensors in order to
                get the prediction. Default: None.
            metric_drop_ths (float, optional): Threshold for the accepted drop
                in terms of precision. Any optimized model with a higher drop
                will be ignored. Default: None.
            quantization_type (QuantizationType, optional): The desired
                quantization algorithm to be used. Default: None.
            input_data (DataManager): User defined data. Default: None.
        """

        if quantization_type not in self.supported_ops[self.device.value]:
            self.compiled_model = None
            return

        if quantization_type is QuantizationType.STATIC and input_data is None:
            raise ValueError("Input data is required for static quantization.")

        self.logger.info(
            f"Optimizing with {self.__class__.__name__} and "
            f"q_type: {quantization_type}."
        )

        check_quantization(quantization_type, metric_drop_ths)
        train_input_data = input_data.get_split("train").get_list(QUANTIZATION_DATA_NUM)

        if quantization_type is not None:
            model = self._quantize_model(
                model, quantization_type, input_tfms, train_input_data
            )

        self.compiled_model = self._compile_model(model, input_data, quantization_type)

    def _compile_model(
        self,
        model: Union[Module, GraphModule],
        input_data: DataManager,
        quantization_type: QuantizationType,
    ) -> ScriptModule:
        print("Compiling model... using Pytorch JIT")
        input_sample = input_data.get_list(1)[0]
        if self.device is Device.GPU:
            if quantization_type is QuantizationType.HALF:
                self.logger.info(
                    f"Converting model to half precision for {self.device.value}."
                )
                self.logger.info(
                    f"original data types = {[t.dtype for t in input_sample]}"
                )
                input_sample = [
                    t.cuda().half() if torch.is_floating_point(t) else t.cuda()
                    for t in input_sample
                ]
                self.logger.info(
                    f"converted data types = {[t.dtype for t in input_sample]}"
                )
            else:
                input_sample = [t.cuda() for t in input_sample]

        if not isinstance(model, torch.fx.GraphModule):
            model.eval()
            try:
                model_scripted = symbolic_trace(model)
                model_scripted = torch.jit.script(model_scripted)
            except Exception:
                try:
                    model_scripted = torch.jit.script(model)
                except Exception:
                    model_scripted = torch.jit.trace(model, input_sample)
        else:
            model_scripted = torch.jit.script(model)

        return model_scripted

    def _quantize_model(
        self,
        model: Module,
        quantization_type: QuantizationType,
        input_tfms: MultiStageTransformation,
        input_data_torch: List[Tuple[torch.Tensor, ...]],
    ):
        return quantize_pytorch(
            model, quantization_type, input_tfms, input_data_torch, self.device
        )
