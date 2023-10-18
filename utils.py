import networkx as nx
import torch_geometric
import numpy as np
import matplotlib.pyplot as plt
import torch
import wandb
from datetime import datetime
from littleballoffur.exploration_sampling import *
from tqdm import tqdm






def wandb_cfg_to_actual_cfg(original_cfg, wandb_cfg):
    """
    Retrive wandb config from saved file
    Args:
        original_cfg: the config from this run
        wandb_cfg: the saved config from the training run

    Returns:
        a config with values updated to those from the saved training run
    """
    original_keys = list(vars(original_cfg).keys())
    wandb_keys = list(wandb_cfg.keys())

    for key in original_keys:
        if key not in wandb_keys:
            continue

        vars(original_cfg)[key] = wandb_cfg[key]['value']

    return original_cfg

def vis_from_pyg(data, filename = None, ax = None):
    """
    Visualise a pytorch_geometric.data.Data object
    Args:
        data: pytorch_geometric.data.Data object
        filename: if passed, this is the filename for the saved image. Ignored if ax is not None
        ax: matplotlib axis object, which is returned if passed

    Returns:

    """
    g, labels = better_to_nx(data)
    if ax is None:
        fig, ax = plt.subplots(figsize = (6,6))
        ax_was_none = True
    else:
        ax_was_none = False

    pos = nx.kamada_kawai_layout(g)

    nx.draw_networkx_edges(g, pos = pos, ax = ax)
    if np.unique(labels).shape[0] != 1:
        nx.draw_networkx_nodes(g, pos=pos, node_color=labels, cmap="tab20", node_size=64,
                               vmin=0, vmax=20, ax=ax)

    ax.axis('off')
    ax.set_title(f"|V|: {g.order()}, |E|: {g.number_of_edges()}")

    plt.tight_layout()

    if not ax_was_none:
        return ax
    elif filename is None:
        plt.show()
    else:
        plt.savefig(filename)
        plt.close()

    plt.close()



def vis_grid(datalist, filename):
    """
    Visualise a set of graphs, from pytorch_geometric.data.Data objects
    Args:
        datalist: list of pyg.data.Data objects
        filename: the visualised grid is saved to this path

    Returns:
        None
    """

    # Trim to square root to ensure square grid
    grid_dim = int(np.sqrt(len(datalist)))

    fig, axes = plt.subplots(grid_dim, grid_dim, figsize=(8,8))

    # Unpack axes
    axes = [num for sublist in axes for num in sublist]

    for i_axis, ax in enumerate(axes):
        ax = vis_from_pyg(datalist[i_axis], ax = ax)

    plt.savefig(filename)

def better_to_nx(data):
    """
    Converts a pytorch_geometric.data.Data object to a networkx graph,
    robust to nodes with no edges, unlike the original pytorch_geometric version

    Args:
        data: pytorch_geometric.data.Data object

    Returns:
        g: a networkx.Graph graph
        labels: torch.Tensor of node labels
    """
    edges = data.edge_index.T.cpu().numpy()
    labels = data.x[:,0].cpu().numpy()

    g = nx.Graph()
    g.add_edges_from(edges)

    for ilabel in range(labels.shape[0]):
        if ilabel not in np.unique(edges):
            g.add_node(ilabel)

    return g, labels

def setup_wandb(cfg):
    """
    Uses a config dictionary to initialise wandb to track sampling.
    Requires a wandb account, https://wandb.ai/

    params: cfg: argparse Namespace

    returns:
    param: cfg: same config
    """

    kwargs = {'name': 'all' + datetime.now().strftime("%m-%d-%Y-%H-%M-%S"), 'project': f'gcl', 'config': cfg,
              'settings': wandb.Settings(_disable_stats=False), 'reinit': True, 'entity':'hierarchical-diffusion', 'mode':'offline'}
    wandb.init(**kwargs)
    wandb.save('*.txt')

    return cfg

def ESWR(graph, n_graphs, size):

    # possible_samplers = inspect.getmembers(samplers, inspect.isclass)
    #
    # possible_samplers = [item[1] for item in possible_samplers]
    possible_samplers = [MetropolisHastingsRandomWalkSampler, DiffusionSampler, DepthFirstSearchSampler]
    sampler_list = []
    for sampler in possible_samplers:
        for i in range(24,96):
            sampler_list.append(sampler(i))
    # # selected_sampler = possible_samplers[np.random.randint(len(possible_samplers))]
    #
    #
    # print(f"Sampling {n_graphs} graphs from {graph}")
    # graphs = []
    # for i in tqdm(range(n_graphs), leave = False):
    #     selected_sampler = possible_samplers[np.random.randint(len(possible_samplers))]
    #     sampler = selected_sampler(number_of_nodes=np.random.randint(12, 48))
    #     graphs.append(nx.convert_node_labels_to_integers(sampler.sample(graph)))
    # sampler = selected_sampler(number_of_nodes=np.random.randint(12, 36))
    # sampler = MetropolisHastingsRandomWalkSampler(48)
    graphs = []
    for i in tqdm(range(n_graphs)):
        sampler = sampler_list[np.random.randint(len(sampler_list))]
        graphs.append(nx.convert_node_labels_to_integers(sampler.sample(graph)))
    # graphs = [nx.convert_node_labels_to_integers(sampler.sample(graph)) for i in tqdm(range(n_graphs))]

    return graphs

if __name__ == "__main__":
    g = nx.erdos_renyi_graph(100, 0.1)

    data = torch_geometric.utils.from_networkx(g)
    data.x = torch.Tensor(np.random.randint(size=(g.order(), 5), high=5, low = 0))


    vis_from_pyg(data)


