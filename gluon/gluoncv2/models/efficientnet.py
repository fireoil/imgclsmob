"""
    EfficientNet for ImageNet-1K, implemented in Gluon.
    Original paper: 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.
"""

__all__ = ['EfficientNet', 'efficientnet_b0', 'efficientnet_b1', 'efficientnet_b2', 'efficientnet_b3',
           'efficientnet_b4', 'efficientnet_b5', 'efficientnet_b6', 'efficientnet_b7', 'efficientnet_b0b',
           'efficientnet_b1b', 'efficientnet_b2b', 'efficientnet_b3b', 'efficientnet_b4b', 'efficientnet_b5b',
           'efficientnet_b6b', 'efficientnet_b7b']

import os
import math
from mxnet import cpu
from mxnet.gluon import nn, HybridBlock
from .common import round_channels, conv1x1_block, conv3x3_block, dwconv3x3_block, dwconv5x5_block, SEBlock


def calc_tf_padding(x,
                    kernel_size,
                    strides=1,
                    dilation=1):
    """
    Calculate TF-same like padding size.

    Parameters:
    ----------
    x : tensor
        Input tensor.
    kernel_size : int
        Convolution window size.
    strides : int, default 1
        Strides of the convolution.
    dilation : int, default 1
        Dilation value for convolution layer.

    Returns
    -------
    tuple of 4 int
        The size of the padding.
    """
    height, width = x.shape[2:]
    oh = math.ceil(height / strides)
    ow = math.ceil(width / strides)
    pad_h = max((oh - 1) * strides + (kernel_size - 1) * dilation + 1 - height, 0)
    pad_w = max((ow - 1) * strides + (kernel_size - 1) * dilation + 1 - width, 0)
    return 0, 0, 0, 0, pad_h // 2, pad_h - pad_h // 2, pad_w // 2, pad_w - pad_w // 2


class EffiDwsConvUnit(HybridBlock):
    """
    EfficientNet specific depthwise separable convolution block/unit with BatchNorms and activations at each convolution
    layers.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    strides : int or tuple/list of 2 int
        Strides of the second convolution layer.
    bn_epsilon : float
        Small float added to variance in Batch norm.
    bn_use_global_stats : bool
        Whether global moving statistics is used instead of local batch-norm for BatchNorm layers.
    activation : str
        Name of activation function.
    tf_mode : bool
        Whether to use TF-like mode.
    """
    def __init__(self,
                 in_channels,
                 out_channels,
                 strides,
                 bn_epsilon,
                 bn_use_global_stats,
                 activation,
                 tf_mode,
                 **kwargs):
        super(EffiDwsConvUnit, self).__init__(**kwargs)
        self.tf_mode = tf_mode
        self.residual = (in_channels == out_channels) and (strides == 1)

        with self.name_scope():
            self.dw_conv = dwconv3x3_block(
                in_channels=in_channels,
                out_channels=in_channels,
                padding=(0 if tf_mode else 1),
                bn_epsilon=bn_epsilon,
                bn_use_global_stats=bn_use_global_stats,
                activation=activation)
            self.se = SEBlock(
                channels=in_channels,
                reduction=4,
                activation=activation)
            self.pw_conv = conv1x1_block(
                in_channels=in_channels,
                out_channels=out_channels,
                bn_epsilon=bn_epsilon,
                bn_use_global_stats=bn_use_global_stats,
                activation=None)

    def hybrid_forward(self, F, x):
        if self.residual:
            identity = x
        if self.tf_mode:
            x = F.pad(x, mode="constant", pad_width=calc_tf_padding(x, kernel_size=3), constant_value=0)
        x = self.dw_conv(x)
        x = self.se(x)
        x = self.pw_conv(x)
        if self.residual:
            x = x + identity
        return x


class EffiInvResUnit(HybridBlock):
    """
    EfficientNet inverted residual unit.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    kernel_size : int or tuple/list of 2 int
        Convolution window size.
    strides : int or tuple/list of 2 int
        Strides of the second convolution layer.
    expansion_factor : int
        Factor for expansion of channels.
    bn_epsilon : float
        Small float added to variance in Batch norm.
    bn_use_global_stats : bool
        Whether global moving statistics is used instead of local batch-norm for BatchNorm layers.
    activation : str
        Name of activation function.
    tf_mode : bool
        Whether to use TF-like mode.
    """
    def __init__(self,
                 in_channels,
                 out_channels,
                 kernel_size,
                 strides,
                 expansion_factor,
                 bn_epsilon,
                 bn_use_global_stats,
                 activation,
                 tf_mode,
                 **kwargs):
        super(EffiInvResUnit, self).__init__(**kwargs)
        self.kernel_size = kernel_size
        self.strides = strides
        self.tf_mode = tf_mode
        self.residual = (in_channels == out_channels) and (strides == 1)
        mid_channels = in_channels * expansion_factor
        dwconv_block_fn = dwconv3x3_block if kernel_size == 3 else (dwconv5x5_block if kernel_size == 5 else None)

        with self.name_scope():
            self.conv1 = conv1x1_block(
                in_channels=in_channels,
                out_channels=mid_channels,
                bn_epsilon=bn_epsilon,
                bn_use_global_stats=bn_use_global_stats,
                activation=activation)
            self.conv2 = dwconv_block_fn(
                in_channels=mid_channels,
                out_channels=mid_channels,
                strides=strides,
                padding=(0 if tf_mode else (kernel_size // 2)),
                bn_epsilon=bn_epsilon,
                bn_use_global_stats=bn_use_global_stats,
                activation=activation)
            self.se = SEBlock(
                channels=mid_channels,
                reduction=24,
                activation=activation)
            self.conv3 = conv1x1_block(
                in_channels=mid_channels,
                out_channels=out_channels,
                bn_epsilon=bn_epsilon,
                bn_use_global_stats=bn_use_global_stats,
                activation=None)

    def hybrid_forward(self, F, x):
        if self.residual:
            identity = x
        x = self.conv1(x)
        if self.tf_mode:
            x = F.pad(x, mode="constant",
                      pad_width=calc_tf_padding(x, kernel_size=self.kernel_size, strides=self.strides), constant_value=0)
        x = self.conv2(x)
        x = self.se(x)
        x = self.conv3(x)
        if self.residual:
            x = x + identity
        return x


class EffiInitBlock(HybridBlock):
    """
    EfficientNet specific initial block.

    Parameters:
    ----------
    in_channels : int
        Number of input channels.
    out_channels : int
        Number of output channels.
    bn_epsilon : float
        Small float added to variance in Batch norm.
    bn_use_global_stats : bool
        Whether global moving statistics is used instead of local batch-norm for BatchNorm layers.
    activation : str
        Name of activation function.
    tf_mode : bool
        Whether to use TF-like mode.
    """

    def __init__(self,
                 in_channels,
                 out_channels,
                 bn_epsilon,
                 bn_use_global_stats,
                 activation,
                 tf_mode,
                 **kwargs):
        super(EffiInitBlock, self).__init__(**kwargs)
        self.tf_mode = tf_mode

        with self.name_scope():
            self.conv = conv3x3_block(
                in_channels=in_channels,
                out_channels=out_channels,
                strides=2,
                padding=(0 if tf_mode else 1),
                bn_epsilon=bn_epsilon,
                bn_use_global_stats=bn_use_global_stats,
                activation=activation)

    def hybrid_forward(self, F, x):
        if self.tf_mode:
            x = F.pad(x, mode="constant", pad_width=calc_tf_padding(x, kernel_size=3, strides=2), constant_value=0)
        x = self.conv(x)
        return x


class EfficientNet(HybridBlock):
    """
    EfficientNet(-B0) model from 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    channels : list of list of int
        Number of output channels for each unit.
    init_block_channels : int
        Number of output channels for initial unit.
    final_block_channels : int
        Number of output channels for the final block of the feature extractor.
    kernel_sizes : list of list of int
        Number of kernel sizes for each unit.
    strides_per_stage : list int
        Stride value for the first unit of each stage.
    expansion_factors : list of list of int
        Number of expansion factors for each unit.
    dropout_rate : float, default 0.2
        Fraction of the input units to drop. Must be a number between 0 and 1.
    tf_mode : bool, default False
        Whether to use TF-like mode.
    bn_epsilon : float, default 1e-5
        Small float added to variance in Batch norm.
    bn_use_global_stats : bool, default False
        Whether global moving statistics is used instead of local batch-norm for BatchNorm layers.
        Useful for fine-tuning.
    in_channels : int, default 3
        Number of input channels.
    in_size : tuple of two ints, default (224, 224)
        Spatial size of the expected input image.
    classes : int, default 1000
        Number of classification classes.
    """
    def __init__(self,
                 channels,
                 init_block_channels,
                 final_block_channels,
                 kernel_sizes,
                 strides_per_stage,
                 expansion_factors,
                 dropout_rate=0.2,
                 tf_mode=False,
                 bn_epsilon=1e-5,
                 bn_use_global_stats=False,
                 in_channels=3,
                 in_size=(224, 224),
                 classes=1000,
                 **kwargs):
        super(EfficientNet, self).__init__(**kwargs)
        self.in_size = in_size
        self.classes = classes
        activation = "swish"

        with self.name_scope():
            self.features = nn.HybridSequential(prefix="")
            self.features.add(EffiInitBlock(
                in_channels=in_channels,
                out_channels=init_block_channels,
                bn_epsilon=bn_epsilon,
                bn_use_global_stats=bn_use_global_stats,
                activation=activation,
                tf_mode=tf_mode))
            in_channels = init_block_channels
            for i, channels_per_stage in enumerate(channels):
                kernel_sizes_per_stage = kernel_sizes[i]
                expansion_factors_per_stage = expansion_factors[i]
                stage = nn.HybridSequential(prefix="stage{}_".format(i + 1))
                with stage.name_scope():
                    for j, out_channels in enumerate(channels_per_stage):
                        kernel_size = kernel_sizes_per_stage[j]
                        expansion_factor = expansion_factors_per_stage[j]
                        strides = strides_per_stage[i] if (j == 0) else 1
                        if i == 0:
                            stage.add(EffiDwsConvUnit(
                                in_channels=in_channels,
                                out_channels=out_channels,
                                strides=strides,
                                bn_epsilon=bn_epsilon,
                                bn_use_global_stats=bn_use_global_stats,
                                activation=activation,
                                tf_mode=tf_mode))
                        else:
                            stage.add(EffiInvResUnit(
                                in_channels=in_channels,
                                out_channels=out_channels,
                                kernel_size=kernel_size,
                                strides=strides,
                                expansion_factor=expansion_factor,
                                bn_epsilon=bn_epsilon,
                                bn_use_global_stats=bn_use_global_stats,
                                activation=activation,
                                tf_mode=tf_mode))
                        in_channels = out_channels
                self.features.add(stage)
            self.features.add(conv1x1_block(
                in_channels=in_channels,
                out_channels=final_block_channels,
                bn_epsilon=bn_epsilon,
                bn_use_global_stats=bn_use_global_stats,
                activation=activation))
            in_channels = final_block_channels
            self.features.add(nn.GlobalAvgPool2D())

            self.output = nn.HybridSequential(prefix="")
            self.output.add(nn.Flatten())
            if dropout_rate > 0.0:
                self.output.add(nn.Dropout(rate=dropout_rate))
            self.output.add(nn.Dense(
                units=classes,
                in_units=in_channels))

    def hybrid_forward(self, F, x):
        x = self.features(x)
        x = self.output(x)
        return x


def get_efficientnet(version,
                     in_size,
                     tf_mode=False,
                     bn_epsilon=1e-5,
                     model_name=None,
                     pretrained=False,
                     ctx=cpu(),
                     root=os.path.join("~", ".mxnet", "models"),
                     **kwargs):
    """
    Create EfficientNet model with specific parameters.

    Parameters:
    ----------
    version : str
        Version of EfficientNet ('b0'...'b7').
    in_size : tuple of two ints
        Spatial size of the expected input image.
    tf_mode : bool, default False
        Whether to use TF-like mode.
    bn_epsilon : float, default 1e-5
        Small float added to variance in Batch norm.
    model_name : str or None, default None
        Model name for loading pretrained model.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """

    if version == "b0":
        assert (in_size == (224, 224))
        depth_factor = 1.0
        width_factor = 1.0
        dropout_rate = 0.2
    elif version == "b1":
        assert (in_size == (240, 240))
        depth_factor = 1.1
        width_factor = 1.0
        dropout_rate = 0.2
    elif version == "b2":
        assert (in_size == (260, 260))
        depth_factor = 1.2
        width_factor = 1.1
        dropout_rate = 0.3
    elif version == "b3":
        assert (in_size == (300, 300))
        depth_factor = 1.4
        width_factor = 1.2
        dropout_rate = 0.3
    elif version == "b4":
        assert (in_size == (380, 380))
        depth_factor = 1.8
        width_factor = 1.4
        dropout_rate = 0.4
    elif version == "b5":
        assert (in_size == (456, 456))
        depth_factor = 2.2
        width_factor = 1.6
        dropout_rate = 0.4
    elif version == "b6":
        assert (in_size == (528, 528))
        depth_factor = 2.6
        width_factor = 1.8
        dropout_rate = 0.5
    elif version == "b7":
        assert (in_size == (600, 600))
        depth_factor = 3.1
        width_factor = 2.0
        dropout_rate = 0.5
    else:
        raise ValueError("Unsupported EfficientNet version {}".format(version))

    init_block_channels = 32
    layers = [1, 2, 2, 3, 3, 4, 1]
    downsample = [1, 1, 1, 1, 0, 1, 0]
    channels_per_layers = [16, 24, 40, 80, 112, 192, 320]
    expansion_factors_per_layers = [1, 6, 6, 6, 6, 6, 6]
    kernel_sizes_per_layers = [3, 3, 5, 3, 5, 5, 3]
    strides_per_stage = [1, 2, 2, 2, 1, 2, 1]
    final_block_channels = 1280

    layers = [int(math.ceil(li * depth_factor)) for li in layers]
    channels_per_layers = [round_channels(ci * width_factor) for ci in channels_per_layers]

    from functools import reduce
    channels = reduce(lambda x, y: x + [[y[0]] * y[1]] if y[2] != 0 else x[:-1] + [x[-1] + [y[0]] * y[1]],
                      zip(channels_per_layers, layers, downsample), [])
    kernel_sizes = reduce(lambda x, y: x + [[y[0]] * y[1]] if y[2] != 0 else x[:-1] + [x[-1] + [y[0]] * y[1]],
                          zip(kernel_sizes_per_layers, layers, downsample), [])
    expansion_factors = reduce(lambda x, y: x + [[y[0]] * y[1]] if y[2] != 0 else x[:-1] + [x[-1] + [y[0]] * y[1]],
                               zip(expansion_factors_per_layers, layers, downsample), [])
    strides_per_stage = reduce(lambda x, y: x + [[y[0]] * y[1]] if y[2] != 0 else x[:-1] + [x[-1] + [y[0]] * y[1]],
                               zip(strides_per_stage, layers, downsample), [])
    strides_per_stage = [si[0] for si in strides_per_stage]

    init_block_channels = round_channels(init_block_channels * width_factor)

    if width_factor > 1.0:
        assert (int(final_block_channels * width_factor) == round_channels(final_block_channels * width_factor))
        final_block_channels = round_channels(final_block_channels * width_factor)

    net = EfficientNet(
        channels=channels,
        init_block_channels=init_block_channels,
        final_block_channels=final_block_channels,
        kernel_sizes=kernel_sizes,
        strides_per_stage=strides_per_stage,
        expansion_factors=expansion_factors,
        dropout_rate=dropout_rate,
        tf_mode=tf_mode,
        bn_epsilon=bn_epsilon,
        in_size=in_size,
        **kwargs)

    if pretrained:
        if (model_name is None) or (not model_name):
            raise ValueError("Parameter `model_name` should be properly initialized for loading pretrained model.")
        from .model_store import get_model_file
        net.load_parameters(
            filename=get_model_file(
                model_name=model_name,
                local_model_store_dir_path=root),
            ctx=ctx)

    return net


def efficientnet_b0(in_size=(224, 224), **kwargs):
    """
    EfficientNet-B0 model from 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (224, 224)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b0", in_size=in_size, model_name="efficientnet_b0", **kwargs)


def efficientnet_b1(in_size=(240, 240), **kwargs):
    """
    EfficientNet-B1 model from 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (240, 240)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b1", in_size=in_size, model_name="efficientnet_b1", **kwargs)


def efficientnet_b2(in_size=(260, 260), **kwargs):
    """
    EfficientNet-B2 model from 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (260, 260)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b2", in_size=in_size, model_name="efficientnet_b2", **kwargs)


def efficientnet_b3(in_size=(300, 300), **kwargs):
    """
    EfficientNet-B3 model from 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (300, 300)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b3", in_size=in_size, model_name="efficientnet_b3", **kwargs)


def efficientnet_b4(in_size=(380, 380), **kwargs):
    """
    EfficientNet-B4 model from 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (380, 380)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b4", in_size=in_size, model_name="efficientnet_b4", **kwargs)


def efficientnet_b5(in_size=(456, 456), **kwargs):
    """
    EfficientNet-B5 model from 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (456, 456)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b5", in_size=in_size, model_name="efficientnet_b5", **kwargs)


def efficientnet_b6(in_size=(528, 528), **kwargs):
    """
    EfficientNet-B6 model from 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (528, 528)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b6", in_size=in_size, model_name="efficientnet_b6", **kwargs)


def efficientnet_b7(in_size=(600, 600), **kwargs):
    """
    EfficientNet-B7 model from 'EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks,'
    https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (600, 600)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b7", in_size=in_size, model_name="efficientnet_b7", **kwargs)


def efficientnet_b0b(in_size=(224, 224), **kwargs):
    """
    EfficientNet-B0-b (like TF-implementation) model from 'EfficientNet: Rethinking Model Scaling for Convolutional
    Neural Networks,' https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (224, 224)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b0", in_size=in_size, tf_mode=True, bn_epsilon=1e-3, model_name="efficientnet_b0b",
                            **kwargs)


def efficientnet_b1b(in_size=(240, 240), **kwargs):
    """
    EfficientNet-B1-b (like TF-implementation) model from 'EfficientNet: Rethinking Model Scaling for Convolutional
    Neural Networks,' https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (240, 240)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b1", in_size=in_size, tf_mode=True, bn_epsilon=1e-3, model_name="efficientnet_b1b",
                            **kwargs)


def efficientnet_b2b(in_size=(260, 260), **kwargs):
    """
    EfficientNet-B2-b (like TF-implementation) model from 'EfficientNet: Rethinking Model Scaling for Convolutional
    Neural Networks,' https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (260, 260)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b2", in_size=in_size, tf_mode=True, bn_epsilon=1e-3, model_name="efficientnet_b2b",
                            **kwargs)


def efficientnet_b3b(in_size=(300, 300), **kwargs):
    """
    EfficientNet-B3-b (like TF-implementation) model from 'EfficientNet: Rethinking Model Scaling for Convolutional
    Neural Networks,' https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (300, 300)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b3", in_size=in_size, tf_mode=True, bn_epsilon=1e-3, model_name="efficientnet_b3b",
                            **kwargs)


def efficientnet_b4b(in_size=(380, 380), **kwargs):
    """
    EfficientNet-B4-b (like TF-implementation) model from 'EfficientNet: Rethinking Model Scaling for Convolutional
    Neural Networks,' https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (380, 380)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b4", in_size=in_size, tf_mode=True, bn_epsilon=1e-3, model_name="efficientnet_b4b",
                            **kwargs)


def efficientnet_b5b(in_size=(456, 456), **kwargs):
    """
    EfficientNet-B5-b (like TF-implementation) model from 'EfficientNet: Rethinking Model Scaling for Convolutional
    Neural Networks,' https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (456, 456)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b5", in_size=in_size, tf_mode=True, bn_epsilon=1e-3, model_name="efficientnet_b5b",
                            **kwargs)


def efficientnet_b6b(in_size=(528, 528), **kwargs):
    """
    EfficientNet-B6-b (like TF-implementation) model from 'EfficientNet: Rethinking Model Scaling for Convolutional
    Neural Networks,' https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (528, 528)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b6", in_size=in_size, tf_mode=True, bn_epsilon=1e-3, model_name="efficientnet_b6b",
                            **kwargs)


def efficientnet_b7b(in_size=(600, 600), **kwargs):
    """
    EfficientNet-B7-b (like TF-implementation) model from 'EfficientNet: Rethinking Model Scaling for Convolutional
    Neural Networks,' https://arxiv.org/abs/1905.11946.

    Parameters:
    ----------
    in_size : tuple of two ints, default (600, 600)
        Spatial size of the expected input image.
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_efficientnet(version="b7", in_size=in_size, tf_mode=True, bn_epsilon=1e-3, model_name="efficientnet_b7b",
                            **kwargs)


def _test():
    import numpy as np
    import mxnet as mx

    pretrained = False

    models = [
        efficientnet_b0,
        efficientnet_b1,
        efficientnet_b2,
        efficientnet_b3,
        efficientnet_b4,
        efficientnet_b5,
        efficientnet_b6,
        efficientnet_b7,
        efficientnet_b0b,
        efficientnet_b1b,
        efficientnet_b2b,
        efficientnet_b3b,
        efficientnet_b4b,
        efficientnet_b5b,
        efficientnet_b6b,
        efficientnet_b7b,
    ]

    for model in models:

        net = model(pretrained=pretrained)

        ctx = mx.cpu()
        if not pretrained:
            net.initialize(ctx=ctx)

        net_params = net.collect_params()
        weight_count = 0
        for param in net_params.values():
            if (param.shape is None) or (not param._differentiable):
                continue
            weight_count += np.prod(param.shape)
        print("m={}, {}".format(model.__name__, weight_count))
        assert (model != efficientnet_b0 or weight_count == 5288548)
        assert (model != efficientnet_b1 or weight_count == 7794184)
        assert (model != efficientnet_b2 or weight_count == 9109994)
        assert (model != efficientnet_b3 or weight_count == 12233232)
        assert (model != efficientnet_b4 or weight_count == 19341616)
        assert (model != efficientnet_b5 or weight_count == 30389784)
        assert (model != efficientnet_b6 or weight_count == 43040704)
        assert (model != efficientnet_b7 or weight_count == 66347960)
        assert (model != efficientnet_b0b or weight_count == 5288548)
        assert (model != efficientnet_b1b or weight_count == 7794184)
        assert (model != efficientnet_b2b or weight_count == 9109994)
        assert (model != efficientnet_b3b or weight_count == 12233232)
        assert (model != efficientnet_b4b or weight_count == 19341616)
        assert (model != efficientnet_b5b or weight_count == 30389784)
        assert (model != efficientnet_b6b or weight_count == 43040704)
        assert (model != efficientnet_b7b or weight_count == 66347960)

        x = mx.nd.zeros((1, 3, net.in_size[0], net.in_size[1]), ctx=ctx)
        y = net(x)
        assert (y.shape == (1, 1000))


if __name__ == "__main__":
    _test()
