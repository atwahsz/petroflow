""" Batch class for core images procrssing """

import os

import numpy as np
import PIL
import cv2

from well_logs.batchflow import FilesIndex, ImagesBatch, action, inbatch_parallel

def _mirror_padding(image, shape):
    new_shape = (np.array(shape) - image.size) * (np.array(shape) - image.size > 0)
    padding_shape = ((new_shape[1], new_shape[1]), (new_shape[0], new_shape[0]), (0, 0))
    image = np.array(image)
    if image.ndim == 2:
        padding_shape = padding_shape[:-1]
    return PIL.Image.fromarray(np.pad(image, padding_shape, mode='reflect'))

def _get_uv_path(path_dl):
    _path = os.path.split(path_dl)
    filename = _path[1]
    dirname_uv = _path[0][:-2] + 'uv'
    path_uv = os.path.join(dirname_uv, filename)
    return path_uv

class CoreIndex(FilesIndex):
    """ FilesIndex that include well name into indices as prefix. """
    def __init__(self, index=None, path=None, *args, **kwargs):
        """ Create index.

        Parameters
        ----------
        index : int, 1-d array-like or callable
            defines structure of FilesIndex
        path : str
            path to folder with wells.
        *args, **kwargs
            parameters of FilesIndex.
        """
        path = path if path is None else os.path.join(path, '*/samples_dl/*.png')
        super().__init__(index, path, *args, **kwargs)

    @staticmethod
    def build_key(fullpathname, no_ext=False):
        """ Create index item from full path name. Well name will be added
        to index as prefix. """
        folder_name = fullpathname
        splitted_path = []
        for _ in range(3):
            folder_name, _key_name = os.path.split(folder_name)
            splitted_path.append(_key_name)
        key_name = splitted_path[2] + '_' + splitted_path[0]
        if no_ext:
            dot_position = key_name.rfind('.')
            dot_position = dot_position if dot_position > 0 else len(key_name)
            key_name = key_name[:dot_position]
        return key_name, fullpathname

class CoreBatch(ImagesBatch):
    """ Batch class for core images processing. Contains core images in daylight (DL)
    and ultraviolet light (UV) and labels for that pairs: 1 if the pair has defects
    and 0 otherwise. Path to images must have the following form:
    '*/well_name/samples_{uv, dl}/*'.

    Parameters
    ----------
    index : DatasetIndex
        Unique identifiers of core images in the batch.

    Attributes
    ----------
    index : DatasetIndex
        Unique identifiers of core images in the batch.
    dl : 1-D ndarray
        Array of 3-D ndarrays with DL images.
    uv : 1-D ndarray
        Array of 3-D ndarrays with UV images.
    labels : 1-D ndarray
        Labels for images.
    """

    components = 'dl', 'uv', 'labels'

    def _get_components(self, index, components=None):
        if components is None:
            components = self.components
        elif isinstance(components, str):
            components = [components]
        pos = self.get_pos(None, components[0], index)
        res = [getattr(self, component)[pos] for component in components]
        if len(res) == 1:
            res = res[0]
        return res

    def _assemble_images(self, all_results, *args, dst=None, **kwargs):
        dst = self.components[:2] if dst is None else dst
        return self._assemble(all_results, *args, dst=dst, **kwargs)

    def _assemble_labels(self, all_results, *args, dst=None, **kwargs):
        dst = self.components[2] if dst is None else dst
        return self._assemble(all_results, *args, dst=dst, **kwargs)

    def _assemble_uv_labels(self, all_results, *args, dst=None, **kwargs):
        dst = self.components[1:] if dst is None else dst
        return self._assemble(all_results, *args, dst=dst, **kwargs)

    @action
    @inbatch_parallel(init='indices', post='_assemble_images')
    def load(self, index, grayscale=False, **kwargs):
        """ Load data.

        Parameters
        ----------
        grayscale : bool
            if True, convert images to gray scale.
        dst : tuple
            components to save resulting images. Default: ('dl', 'uv').
        """
        full_path_dl = self._get_file_name(index, src=None)
        full_path_uv = _get_uv_path(full_path_dl)
        res = (PIL.Image.open(full_path_dl), PIL.Image.open(full_path_uv))
        if grayscale:
            res = [item.convert('L') for item in res]
        return res[0], res[1]

    @action
    @inbatch_parallel(init='indices', post='_assemble_labels')
    def create_labels(self, index, labels=None, **kwargs):
        """ Create labels from pd.DataSeries/dict

        Parameters
        ----------
        labels : pd.DataSeries/dict
            index/keys should correspond to index of the Dataset
        dst : str
            components to save resulting images. Default: 'labels'.
        """
        _ = self, kwargs
        label = 0 if labels is None else labels[index]
        return label

    @action
    @inbatch_parallel(init='indices', post='_assemble_images')
    def mirror_padding(self, index, shape, src=None, **kwargs):
        """ Add padding to images with the size which is less then shape.
        Parameters
        ----------
        shape : tuple
            if image shape is less than size, image will be padded by reflections.
        src : tuple of str
            components to process. Default: ('dl', 'uv').
        dst : tuple of str
            components to save resulting images. Default: ('dl', 'uv').
        """
        _ = kwargs
        src = self.components[:2] if src is None else src
        return [_mirror_padding(img, shape) for img in self._get_components(index, src)]

    @action
    @inbatch_parallel(init='indices', post='_assemble_images')
    def fix_shape(self, index, src=None, **kwargs):
        """ Transform shapes of DL and UV to the same values. Image with larger
        shape will be croppped.

        Parameters
        ----------
        src : tuple of str
            components to process. Default: ('dl', 'uv').
        dst : tuple of str
            components to save resulting images. Default: ('dl', 'uv').
        """
        _ = kwargs
        src = self.components[:2] if src is None else src
        images = [np.array(img) for img in self._get_components(index, src)]
        shape = (min(*[img.shape[0] for img in images]), min(*[img.shape[1] for img in images]))
        images = [PIL.Image.fromarray(img[:shape[0], :shape[1]]) for img in images]
        return images

    @action
    @inbatch_parallel(init='indices', post='_assemble_uv_labels')
    def flip_uv(self, index, proba=0.5, src=None, **kwargs):
        """ Randomly flip UV images. Flipped images always will have label 1.

        Parameters
        ----------
        proba : float
            probability of flip.
        src : tuple of str
            components to process. Default: ('uv', 'labels').
        dst : tuple of str
            components to save resulting images and labels. Default: ('uv', 'labels').
        """
        _ = kwargs
        src = self.components[1:] if src is None else src
        img, label = self._get_components(index, src)
        if np.random.rand() < proba:
            img = PIL.ImageOps.flip(img)
            label = 1.
        return img, label

    @action
    def shuffle_images(self, proba=0.5, src=None, dst=None):
        """ Shuffle DL and UV images. Shuffled images will have label 1.

        Parameters
        ----------
        proba : float
            probability that pair in the batch will be changed.
        src : tuple of str
            components to process. Default: ('dl', uv', 'labels').
        dst : tuple of str
            components to save resulting images and labels. Default: ('dl', 'uv', 'labels').
        """
        n_permutations = int(np.ceil(len(self.indices) * proba / 2))
        shuffled_indices = np.random.choice(self.indices, n_permutations, replace=False)
        src = self.components if src is None else src
        dst = self.components if dst is None else dst
        for i, component in enumerate(src):
            setattr(self, dst[i], getattr(self, component))
        for i, j in zip(self.indices[:n_permutations], shuffled_indices):
            if i != j:
                uv1 = self._get_components(i, src[1])
                uv2 = self._get_components(j, src[1])
                getattr(self, dst[1])[self.get_pos(None, src[0], i)] = uv2
                getattr(self, dst[1])[self.get_pos(None, src[0], j)] = uv1
                getattr(self, dst[2])[self.get_pos(None, src[0], i)] = 1
                getattr(self, dst[2])[self.get_pos(None, src[0], j)] = 1
        return self

    @action
    @inbatch_parallel(init='indices', post='_assemble_images')
    def normalize(self, index, src=None, **kwargs):
        """ Normalize images histograms.

        Parameters
        ----------
        src : tuple of str
            components to process. Default: ('dl', uv').
        dst : tuple of str
            components to save resulting images. Default: ('dl', 'uv').
        """
        _ = kwargs
        res = []
        src = self.components[:2] if src is None else src
        for component in src:
            pos = self.get_pos(None, component, index)
            image = np.array(getattr(self, component)[pos])
            res.append(cv2.equalizeHist(image)) # pylint: disable=no-member
        return res

    @action
    @inbatch_parallel(init='indices', post='_assemble_images', target="threads")
    def random_crop(self, index, length, n_crops=1, src=None, **kwargs):
        """ Get random crops from images.

        Parameters
        ----------
        length : int
            length of the crop.
        n_crops : int
            number of crops from one image
        src : tuple of str
            components to process. Default: ('dl', uv').
        dst : tuple of str
            components to save resulting images and labels. Default: ('dl', 'uv').
        """
        _ = kwargs
        src = self.components[:2] if src is None else src
        images = self._get_components(index, src)
        slices = [[slice(None)] * images[0].ndim] * n_crops
        image_length = min([img.shape[-2] for img in images])
        pos = np.random.randint(0, image_length - length + 1, size=n_crops)
        for j, position in enumerate(pos):
            slices[j][-2] = slice(position, position + length)
        return [np.array([img[_slice] for _slice in slices]) for img in images]

    @action
    @inbatch_parallel(init='indices', post='_assemble_images')
    def crop(self, index, length, step, src=None, **kwargs):
        """ Get crops from images.

        Parameters
        ----------
        length : tuple
            length of crop
        step : float
            step between crops
        src : tuple of str
            components to process. Default: ('dl', uv').
        dst : tuple of str
            components to save resulting images and labels. Default: ('dl', 'uv').
        """
        _ = kwargs
        src = self.components[:2] if src is None else src
        images = self._get_components(index, src)
        image_length = min([img.shape[-2] for img in images])
        pos = np.arange(0, image_length-length + 1, step)
        slices = [[slice(None)] * images[0].ndim] * len(pos)
        for j, position in enumerate(pos):
            slices[j][-2] = slice(position, position + length)
        return [np.array([img[_slice] for _slice in slices]) for img in images]
