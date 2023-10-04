import csv
from datetime import datetime
import os
import pathlib
import platform
import subprocess
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True' # needed to avoid Jupyter kernal crash due to matplotlib
from typing import Callable, Mapping, MutableMapping, Optional, Tuple, Dict, List, Sequence, Any, Type
from itertools import groupby, chain
from collections import OrderedDict, defaultdict
import os
import requests
import sys
import numpy as np
from collections import defaultdict
import math
import psutil
from itertools import zip_longest
import random
import hashlib
import json
from dataclasses import dataclass
import importlib
import multiprocessing
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
import logging

import yaml

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.cm as cm

import torch
import torch.nn as nn


# We setup env variable if debugging mode is detected for vs_code_debugging.
# The reason for this is that when Python multiprocessing is used, the new process
# spawned do not inherit 'pydevd' so those process do not get detected as in debugging mode
# even though they are. So we set env var which does get inherited by sub processes.
if 'pydevd' in sys.modules:
    os.environ['vs_code_debugging'] = 'True'
def is_debugging()->bool:
    return 'vs_code_debugging' in os.environ and os.environ['vs_code_debugging']=='True'

def full_path(path:str, create=False)->str:
    assert path
    path = os.path.realpath(
            os.path.expanduser(
                os.path.expandvars(path)))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def setup_sys(seed, max_threads=None):
    os.environ['NUMEXPR_MAX_THREADS'] = str(psutil.cpu_count(logical=False) // 2) if max_threads is None else str(max_threads)
    np.random.seed(seed)
    random.seed(seed)

def median(values):
    values = sorted(values)
    size = len(values)
    if size % 2 == 1:
        return values[int((size - 1) / 2)]
    return (values[int(size / 2 - 1)] + values[int(size / 2)]) / 2


class ExponentialMovingAverage:
    def __init__(self, weight=0.9, initial_value=0.):
        self.value: float = initial_value
        self.n: int = 0
        self.weight = weight
        self.last_good_value, self.last_good_n = self.value, self.n

    def add(self, x: float) -> float:
        if not math.isnan(self.value):
            self.last_good_value, self.last_good_n = self.value, self.n
        self.n += 1
        self.value = x * self.weight + self.last_good_value * (1 - self.weight)
        return self.value

class SmoothedDyDx:
    def __init__(self, y_ema_weight=0.8, x_ema_weight=0.8,
                 dy_ema_weight=0.9, dx_ema_weight=0.9,
                 dydx_ema_weight=0.95):


        self.value = 0.
        self.n = 0

        # smooth x and y
        self.y = ExponentialMovingAverage(y_ema_weight)
        self.x = ExponentialMovingAverage(x_ema_weight)

        # smooth deltas
        self.dy = ExponentialMovingAverage(dy_ema_weight)
        self.dx = ExponentialMovingAverage(dx_ema_weight)

        # smooth dy/dx
        self.dydx = ExponentialMovingAverage(dydx_ema_weight)


    def add(self, y: float, x: float) -> float:
        last_x, last_y = self.x.value, self.y.value

        self.y.add(y)
        self.x.add(x)

        dydx = 0.
        if self.x.n > 1:
            self.dy.add(self.y.value - last_y)
            self.dx.add(self.x.value - last_x)

            dydx = self.dydx.add(self.dy.value / self.dx.value)

        self.value = dydx
        self.n += 1

        return dydx

def save_list(l, filename):
    with open(filename, 'w') as f:
        for item in l:
            if isinstance(item, Sequence):
                for i in item:
                    f.write(f"{i}\t")
            else:
                f.write(f"{item}")
            f.write("\n")

def tensor_hash(tensor, sort=False):
    flattened = tensor.clone().detach().flatten()
    if sort:
        # Flatten and sort the tensor
        flattened, _ = torch.sort(flattened)

    # Convert to byte representation
    tensor_bytes = flattened.cpu().numpy().tobytes()

    # Compute the hash
    return hashlib.sha256(tensor_bytes).hexdigest()

def shuffle_tuple_of_lists(t:Tuple[List, ...])->Tuple[List, ...]:
    # Length of any member
    length = len(t[0])

    # Generate a permutation of indices
    permuted_indices = list(range(length))
    random.shuffle(permuted_indices)

    # Reorder each member of the tuple using the permuted indices
    shuffled = tuple([member[permuted_indices] for member in t]) # type: ignore

    return shuffled

def save_dataloader(dl, filename: str):
    with open(filename, 'w') as f:
        for b in dl:
            inputs, labels = tuple(t for t in b)
            assert(len(inputs)==len(labels))
            for i,l in zip(inputs.tolist(), labels.tolist()):
                for num in i+[l]:
                    f.write(f"{num}\t")
                f.write("\n")

def load_json(doc):
    """Load json that could possibly be malformed"""
    try:
        return json.loads(doc)
    except:
        return None

def uhgroupby(iterable, key:Callable):
    """Group by key and return a dict of iterables"""
    return groupby(sorted(iterable, key=key), key=key)

def ugroupby(iterable, key:Callable, gather:Callable=lambda d,k,g: list(g)):
    d = {}
    for k, g in groupby(iterable, key=key):
        d[k] = gather(d, k, g)
    return d


def draw_histogram(data, xlabel='Values', ylabel='Frequency', title='Histogram', bins=None, log_x=False, log_y=False):
    unique_vals = np.unique(data)
    if len(unique_vals) < 10:
        bins = len(unique_vals)  # If the unique values are less than 10, make a bin for each
    elif bins is None:  # Automatic bin sizing using Freedman-Diaconis rule
        q75, q25 = np.percentile(data, [75 ,25])
        iqr = q75 - q25
        bin_width = 2 * iqr / (len(data) ** (1/3))
        bins = min(round((np.max(data) - np.min(data)) / bin_width), 1000)

    n, bins, patches = plt.hist(data, bins=bins, edgecolor='black')

    # Create a normalization object which scales data values to the range [0, 1]
    fracs = n / n.max()
    norm = mcolors.Normalize(fracs.min(), fracs.max())

    # Assigning a color for each bar using the 'viridis' colormap
    for thisfrac, thispatch in zip(fracs, patches):
        color = cm.viridis(norm(thisfrac)) # type: ignore
        thispatch.set_facecolor(color)

    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.title(title)

    if log_x:
        plt.xscale('log')
    if log_y:
        plt.yscale('log')

    plt.show()


@dataclass
class TorchInfo:
    is_cuda:bool # same as device_type=='cuda'
    is_distributed: bool
    device_type:str
    dtype:str # floating point type
    device_name:str # this can include GPU ID
    rank: int
    local_rank: int
    world_size: int
    is_master: bool
    seed_offset: int
    gradient_accumulation_steps: int
    pt_dtype: torch.dtype

def setup_torch(seed:int,
    device_type:str,
    dtype:str,
    enable_distributed:bool,
    distributed_backend:str, # ex 'nccl
    distributed_init_method:str, # ex 'env://'
    print_precision:int=10,
    gradient_accumulation_steps_1gpu:int=1)->TorchInfo:

    # below is currently disabled because of this bug: https://github.com/pytorch/pytorch/issues/110331
    # show Tensor shape first for tensor's rpresentation
    # normal_repr = torch.Tensor.__repr__
    # torch.Tensor.__repr__ = lambda self: f"{tuple(self.shape)}:{normal_repr(self)}" # type: ignore
    torch.set_printoptions(precision=print_precision)
    #torch._dynamo.config.log_level = logging.WARN

    assert device_type != 'cuda' or (device_type == 'cuda' and torch.cuda.is_available()), 'cuda not available. Set device_type=cpu.'
    assert (device_type != 'cuda' or dtype != 'bfloat16') or (device_type == 'cuda' and dtype == 'bfloat16' and torch.cuda.is_bf16_supported()), 'bfloat16 not supported on your cuda device. Use float16 or float32.'

    is_cuda = device_type == 'cuda'
    device_name = device_type # we will add GPU ID later
    pt_dtype = {'float32': torch.float32, 'bfloat16': torch.bfloat16, 'float16': torch.float16}[dtype]

    if is_cuda: # setup cuda
        torch.backends.cudnn.enabled = True
        torch.backends.cuda.matmul.allow_tf32 = True # allow tf32 on matmul
        torch.backends.cudnn.allow_tf32 = True # allow tf32 on cudnn
        torch.set_float32_matmul_precision('high')

    if enable_distributed:
        assert torch.distributed.is_available(), 'Distributed training not available. Set enable_distributed=False.'
        env_rank = os.environ.get('RANK', '-1')
        if env_rank=='-1':
            raise ValueError('RANK environment variable not set BUT enable_distributed=True. You probably want to launch this script using torch.distributed.launch.')

        torch.distributed.init_process_group(backend=distributed_backend, init_method=distributed_init_method)

        is_distributed = True
        rank = torch.distributed.get_rank()
        local_rank = torch.distributed.get_rank()
        world_size = torch.distributed.get_world_size()
        is_master = rank == 0
        seed_offset = rank

        assert gradient_accumulation_steps_1gpu % world_size == 0, f'gradient_accumulation_steps ({gradient_accumulation_steps_1gpu}) must be divisible by ddp_world_size ({ddp_world_size})'
        gradient_accumulation_steps = gradient_accumulation_steps_1gpu // world_size

        if is_cuda:
            torch.cuda.set_device(local_rank)
            device_name = f'cuda:{local_rank}'
    else:
        is_distributed = False
        rank = 0
        local_rank = 0
        world_size = 1
        is_master = True
        seed_offset = 0
        gradient_accumulation_steps = gradient_accumulation_steps_1gpu


    if is_cuda:
        torch.cuda.manual_seed(seed+seed_offset)
    torch.manual_seed(seed+seed_offset)

    assert (not enable_distributed) or (enable_distributed and torch.distributed.is_initialized()), 'Distributed training not initialized. Call torch.distributed.init_process_group() first.'

    return TorchInfo(is_cuda=is_cuda, is_distributed=is_distributed,
                     device_type=device_type, dtype=dtype, device_name=device_name,
                     rank=rank, local_rank=local_rank, world_size=world_size,
                     is_master=is_master, seed_offset=seed_offset,
                     gradient_accumulation_steps=gradient_accumulation_steps,
                     pt_dtype=pt_dtype)

def save_checkpoint(out_dir:str, name:str, model, optimizer, scheduler,
                    step:int, best_val_loss:float)->str:
    checkpoint = {'model': model.state_dict(),
                  'optimizer': optimizer.state_dict(),
                  'scheduler': scheduler.state_dict(),
                  'step': step,
                  'best_val_loss': best_val_loss}

    out_dir = full_path(out_dir, create=True)
    checkpoint_filepath = os.path.join(out_dir, f'{name}.pt')
    torch.save(checkpoint, checkpoint_filepath)
    return checkpoint_filepath

def import_fn(spec:str)->Callable:
    """Import a function from a module. The spec is in the form of module.submodule.function"""
    module_name, fn_name = spec.rsplit('.', 1)
    module = importlib.import_module(module_name)
    fn = getattr(module, fn_name)
    return fn

def get_stats(nums):
    if isinstance(nums, torch.Tensor):
        nums = nums.detach().cpu().numpy()
    if isinstance(nums, list):
        nums = np.array(nums)
    nums = np.array(nums)
    return {'mean': np.mean(nums),
            'median': np.median(nums),
            'std': np.std(nums),
            'min': np.min(nums),
            'max': np.max(nums),
            'n': len(nums),
            'sum': np.sum(nums),}

def for_parallel(l:list, f:Callable[[Any], Any], num_cpus=multiprocessing.cpu_count() - 1)->list:
    """Calls f() on each element of list l in parallel using num_cpus CPUs"""
    if num_cpus < 1:
        num_cpus = 1  # Make sure at least one CPU is used

    # Calculate size of each slice
    slice_size = len(l) // num_cpus
    slices = [l[i * slice_size:(i + 1) * slice_size] for i in range(num_cpus)]
    # If there are remaining elements, add them to the last slice
    remaining = len(l) % num_cpus
    if remaining:
        slices[-1].extend(l[-remaining:])

    # Function to process each slice
    def process_slice(slice_data):
        return [f(x) for x in slice_data]

    # Perform parallel computation
    with multiprocessing.Pool(num_cpus) as pool:
        result_slices = pool.map(process_slice, slices)

    # Combine the slices back into a single list
    result = [x for sublist in result_slices for x in sublist]

    return result

def transformer_tflops(batch_size, iterations, dt,
                      param_count, n_layer, n_head, n_embd, context_length)->float:
    """ estimate model flops utilization in TFLOPS """

    # first estimate the number of flops we do per iteration.
    # see PaLM paper Appendix B as ref: https://arxiv.org/abs/2204.02311
    N = param_count
    L, H, Q, T = n_layer, n_head, n_embd//n_head, context_length

    flops_per_token = 6*N + 12*L*H*Q*T

    flops_per_fwdbwd = flops_per_token * T

    flops = flops_per_fwdbwd * batch_size * iterations

    # express our flops throughput as ratio of A100 bfloat16 peak flops
    flops_achieved = flops * (1.0/dt) # per second

    return flops_achieved / 1.0E12 # TFLOPS

def cpu_count()->int:
    return multiprocessing.cpu_count()

def work_cpu_count()->int:
    """Returns the number of CPUs to use for work so that we leave some CPUs for the OS and other processes"""
    count = cpu_count()
    if count > 1:
        return count - 1
    else:
        return count

def module_params(module:torch.nn.Module, non_embedding=True):
    filter_params = set()
    if non_embedding:
        for m in module.modules():
            if isinstance(m, nn.Embedding):
                for p in m.parameters():
                    filter_params.add(p)
    for p in module.parameters():
        if p not in filter_params:
            yield p

def module_params_count(module:torch.nn.Module, non_embedding=True)->int:
    n_params = sum(p.numel() for p in module_params(module, non_embedding))
    return n_params

def weight_norm(module:torch.nn.Module, non_embedding=True)->float:
    return torch.linalg.norm(torch.cat([p.view(-1) for p in module_params(module, non_embedding)])).item()

def save_yaml(obj, filepath:str):
    with open(filepath, 'w', encoding='utf-8') as f:
        yaml.dump(obj, f, default_flow_style=False)

def load_yaml(filepath:str)->Any:
    with open(full_path(filepath), 'r', encoding='utf-8') as f:
        return yaml.load(f, Loader=yaml.FullLoader)

def dedup_list(l: List) -> List:
    return list(OrderedDict.fromkeys(l))


def delete_file(filepath: str) -> bool:
    if os.path.isfile(filepath):
        os.remove(filepath)
        return True
    else:
        return False

def path2uri(path: str, windows_non_standard: bool = False) -> str:
    uri = pathlib.Path(full_path(path)).as_uri()

    # there is lot of buggy regex based code out there which expects Windows file URIs as
    # file://C/... instead of standard file:///C/...
    # When passing file uri to such code, turn on windows_non_standard
    if windows_non_standard and is_windows():
        uri = uri.replace('file:///', 'file://')
    return uri


def uri2path(file_uri: str, windows_non_standard: bool = False) -> str:
    # there is lot of buggy regex based code out there which expects Windows file URIs as
    # file://C/... instead of standard file:///C/...
    # When passing file uri to such code, turn on windows_non_standard
    if windows_non_standard and is_windows():
        file_uri = file_uri.replace('file://', 'file:///')

    parsed = urlparse(file_uri)
    host = "{0}{0}{mnt}{0}".format(os.path.sep, mnt=parsed.netloc)
    return os.path.normpath(
        os.path.join(host, url2pathname(unquote(parsed.path)))
    )

def deep_comp(o1: Any, o2: Any) -> bool:
    # NOTE: dict don't have __dict__
    o1d = getattr(o1, '__dict__', None)
    o2d = getattr(o2, '__dict__', None)

    # if both are objects
    if o1d is not None and o2d is not None:
        # we will compare their dictionaries
        o1, o2 = o1.__dict__, o2.__dict__

    if o1 is not None and o2 is not None:
        # if both are dictionaries, we will compare each key
        if isinstance(o1, dict) and isinstance(o2, dict):
            for k in set().union(o1.keys(), o2.keys()):
                if k in o1 and k in o2:
                    if not deep_comp(o1[k], o2[k]):
                        return False
                else:
                    return False  # some key missing
            return True
    # mismatched object types or both are scalers, or one or both None
    return o1 == o2

def zero_file(filepath) -> None:
    """Creates or truncates existing file"""
    open(filepath, 'w').close()


def write_string(filepath: str, content: str) -> None:
    pathlib.Path(filepath).write_text(content)


def read_string(filepath: str) -> str:
    return pathlib.Path(filepath).read_text()

def fmt(val: Any) -> str:
    if isinstance(val, float):
        return f'{val:.4g}'
    return str(val)

def append_csv_file(filepath: str, new_row: List[Tuple[str, Any]], delimiter='\t'):
    fieldnames, rows = [], []
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            dr = csv.DictReader(f, delimiter=delimiter)
            fieldnames = dr.fieldnames
            rows = [row for row in dr.reader]
    if fieldnames is None:
        fieldnames = []

    new_fieldnames = OrderedDict([(fn, None) for fn, v in new_row])
    for fn in fieldnames:
        new_fieldnames[fn] = None

    with open(filepath, 'w', newline='') as f:
        dr = csv.DictWriter(f, fieldnames=new_fieldnames.keys(), delimiter=delimiter)
        dr.writeheader()
        for row in rows:
            d = dict((k, v) for k, v in zip(fieldnames, row))
            dr.writerow(d)
        dr.writerow(OrderedDict(new_row))


def has_method(o, name):
    return callable(getattr(o, name, None))

def extract_tar(src, dest=None, gzip=None, delete=False):
    import tarfile

    if dest is None:
        dest = os.path.dirname(src)
    if gzip is None:
        gzip = src.lower().endswith('.gz')

    mode = 'r:gz' if gzip else 'r'
    with tarfile.open(src, mode) as tarfh:
        tarfh.extractall(path=dest)

    if delete:
        os.remove(src)


def extract_zip(src, dest=None, delete=False):
    import zipfile

    if dest is None:
        dest = os.path.dirname(src)

    with zipfile.ZipFile(src, 'r') as zip_ref:
        zip_ref.extractall(dest)

    if delete:
        os.remove(src)

def cuda_device_names() -> str:
    return ', '.join([torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())])


def exec_shell_command(command: str, print_command_start=True, print_command_end=True) -> subprocess.CompletedProcess:
    if print_command_start:
        print(f'[{datetime.now()}] Running: {command}')

    ret = subprocess.run(command, shell=True, check=True)

    if print_command_end:
        print(f'[{datetime.now()}] returncode={ret.returncode} Finished: {command}')

    return ret


def zip_eq(*iterables):
    sentinel = object()
    for count, combo in enumerate(zip_longest(*iterables, fillvalue=sentinel)):
        if any(True for c in combo if sentinel is c):
            shorter_its = ','.join([str(i) for i, c in enumerate(combo) if sentinel is c])
            raise ValueError(f'Iterator {shorter_its} have length {count} which is shorter than others')
        yield combo

def filepath_without_ext(filepath: str) -> str:
    """Returns '/a/b/c/d.e' for '/a/b/c/d.e.f' """
    return str(pathlib.Path(filepath).with_suffix(''))


def filepath_ext(filepath: str) -> str:
    """Returns '.f' for '/a/b/c/d.e.f' """
    return pathlib.Path(filepath).suffix


def filepath_name_ext(filepath: str) -> str:
    """Returns 'd.e.f' for '/a/b/c/d.e.f' """
    return pathlib.Path(filepath).name


def filepath_name_only(filepath: str) -> str:
    """Returns 'd.e' for '/a/b/c/d.e.f' """
    return pathlib.Path(filepath).stem


def change_filepath_ext(filepath: str, new_ext: str) -> str:
    """Returns '/a/b/c/d.e.g' for filepath='/a/b/c/d.e.f', new_ext='.g' """
    return str(pathlib.Path(filepath).with_suffix(new_ext))


def change_filepath_name(filepath: str, new_name: str, new_ext: Optional[str] = None) -> str:
    """Returns '/a/b/c/h.f' for filepath='/a/b/c/d.e.f', new_name='h' """
    ext = new_ext or filepath_ext(filepath)
    return str(pathlib.Path(filepath).with_name(new_name).with_suffix(ext))

def process_name() -> str:
    return multiprocessing.current_process().name


def is_windows() -> bool:
    return platform.system() == 'Windows'

def deep_update(d: MutableMapping, u: Mapping, map_type: Type[MutableMapping] = dict) -> MutableMapping:
    for k, v in u.items():
        if isinstance(v, Mapping):
            d[k] = deep_update(d.get(k, map_type()), v, map_type)
        else:
            d[k] = v
    return d

def is_directory_empty(path):
    path = full_path(path)
    if not os.path.isdir(path):
        return True
    return not bool(os.listdir(path))

def download_file(url, filename):
    """
    Download a file from a given URL and save it as the specified filename.

    Parameters:
    - url (str): The URL of the file to be downloaded.
    - filename (str): The name with which the file should be saved.
    """

    # Make a GET request to fetch the raw HTML content
    response = requests.get(url, stream=True)
    response.raise_for_status()

    with open(filename, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)

# Example usage:
# download_file("https://www.example.com/sample.jpg", "sample.jpg")
