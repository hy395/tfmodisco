from __future__ import division, print_function, absolute_import
import sklearn
from . import phenograph as ph
import numpy as np
import time
import sys


class ClusterResults(object):

    def __init__(self, cluster_indices, **kwargs):
        self.cluster_indices = cluster_indices 
        self.__dict__.update(kwargs)

    def remap(self, mapping):
        return ClusterResults(cluster_indices=
                np.array([mapping[x] if x in mapping else x
                 for x in self.cluster_indices]))

    def save_hdf5(self, grp):
        grp.attrs["class"] = type(self).__name__
        grp.create_dataset("cluster_indices", data=self.cluster_indices)


class LouvainClusterResults(ClusterResults):

    def __init__(self, cluster_indices, level_to_return, Q):
        super(LouvainClusterResults, self).__init__(
         cluster_indices=cluster_indices)
        self.level_to_return = level_to_return
        self.Q = Q

    def save_hdf5(self, grp):
        grp.attrs["class"] = type(self).__name__
        grp.create_dataset("cluster_indices", data=self.cluster_indices)
        grp.attrs["level_to_return"] = self.level_to_return
        grp.attrs["Q"] = self.Q


class AbstractAffinityMatClusterer(object):

    def __call__(self, affinity_mat):
        raise NotImplementedError()


class PhenographCluster(AbstractAffinityMatClusterer):

    def __init__(self, k=30, min_cluster_size=10, jaccard=True,
                       primary_metric='euclidean',
                       n_jobs=-1, q_tol=0.0, louvain_time_limit=2000,
                       nn_method='kdtree'):
        self.k = k
        self.min_cluster_size = min_cluster_size
        self.jaccard = jaccard
        self.primary_metric = primary_metric
        self.n_jobs = n_jobs
        self.q_tol = q_tol
        self.louvain_time_limit = louvain_time_limit
        self.nn_method = nn_method
    
    def __call__(self, affinity_mat):
        communities, graph, Q, = ph.cluster.cluster(
            data=affinity_mat,
            k=self.k, min_cluster_size=self.min_cluster_size,
            jaccard=self.jaccard, primary_metric=self.primary_metric,
            n_jobs=self.n_jobs, q_tol=self.q_tol,
            louvain_time_limit=self.louvain_time_limit,
            nn_method=self.nn_method)
        return LouvainClusterResults(
                cluster_indices=communities,
                Q=Q)
        

class LouvainCluster(AbstractAffinityMatClusterer):

    def __init__(self, level_to_return=-1,
                       affmat_transformer=None, min_cluster_size=10,
                       max_clusters=None,
                       contin_runs=100,
                       q_tol=0.0, louvain_time_limit=2000,
                       verbose=True, seed=1234):
        self.level_to_return = level_to_return
        self.affmat_transformer = affmat_transformer
        self.min_cluster_size = min_cluster_size
        self.max_clusters = max_clusters
        self.q_tol = q_tol
        self.contin_runs = contin_runs
        self.louvain_time_limit = louvain_time_limit
        self.verbose = verbose
        self.seed=seed
    
    def __call__(self, orig_affinity_mat):

        #replace nan values with zeros
        orig_affinity_mat = np.nan_to_num(orig_affinity_mat)
        assert np.min(orig_affinity_mat) >= 0, np.min(orig_affinity_mat)
        
        # use transformer to compute Louvain-based affinity
        if (self.verbose):
            print("Beginning preprocessing + Louvain")
            sys.stdout.flush()
        all_start = time.time()
        if (self.affmat_transformer is not None):
            affinity_mat = self.affmat_transformer(orig_affinity_mat)
        else:
            affinity_mat = orig_affinity_mat
                
        # compute Louvain clustering based on Louvain-based affinity matrix
        communities, graph, Q, =\
            ph.cluster.runlouvain_given_graph(
                graph=affinity_mat,
                level_to_return=self.level_to_return,
                min_cluster_size=self.min_cluster_size,
                max_clusters=self.max_clusters,
                q_tol=self.q_tol,
                contin_runs=self.contin_runs,
                louvain_time_limit=self.louvain_time_limit,
                seed=self.seed)
        
        cluster_results = LouvainClusterResults(
                cluster_indices=communities,
                level_to_return=self.level_to_return,
                Q=Q)

        if (self.verbose):
            print("Preproc + Louvain took "+str(time.time()-all_start)+" s")
            sys.stdout.flush()
        return cluster_results


class HDbScanCluster(AbstractAffinityMatClusterer):

    def __init__(self, aff_to_dist_mat):
        self.aff_to_dist_mat = aff_to_dist_mat

    def __call__(self, orig_affinity_mat):
        import hdbscan

        dist_mat = self.aff_to_dist_mat(orig_affinity_mat) 
        clusterer = hdbscan.HDBSCAN(metric='precomputed')
        clusterer.fit(dist_mat)
        return ClusterResults(cluster_indices=clusterer.labels_)
 

class CollectComponents(AbstractAffinityMatClusterer):

    def __init__(self, dealbreaker_threshold,
                       join_threshold, min_cluster_size,
                       max_neighbors_to_check=500, transformer=None,
                       verbose=True):
        self.dealbreaker_threshold = dealbreaker_threshold
        self.join_threshold = join_threshold
        self.min_cluster_size = min_cluster_size
        self.transformer = transformer
        self.max_neighbors_to_check = max_neighbors_to_check
        self.verbose = verbose

    def __call__(self, affinity_mat):

        if (self.transformer is not None):
            if (self.verbose):
                print("Applying transformation")
                sys.stdout.flush()
            affinity_mat = self.transformer(affinity_mat)
            if (self.verbose):
                print("Transformation done")
                sys.stdout.flush()

        #start off with each node in its own cluster
        idx_to_others_in_cluster = dict([(i, set([i])) for i in
                                         range(len(affinity_mat))])
        cached_incompatibilities = (affinity_mat < self.dealbreaker_threshold) 

        sorted_pairs = sorted([(i,j,affinity_mat[i,j])
                               for i in range(len(affinity_mat))
                               for j in range(len(affinity_mat)) if
                               ((i < j) and
                                affinity_mat[i,j] >= self.join_threshold)],
                              key=lambda x: -x[2])

        count = 0
        for (i,j,sim) in sorted_pairs:
            if (i not in idx_to_others_in_cluster[j] and
                 cached_incompatibilities[i,j]==0):
                #try joining

                dealbreaker = False
                #for scalability, cap the number of neighbors we compare
                to_compare1 = idx_to_others_in_cluster[i]
                if (len(to_compare1) > self.max_neighbors_to_check):
                    #take a subset
                    to_compare1 = list(to_compare1)\
                                   [:self.max_neighbors_to_check]

                to_compare2 = idx_to_others_in_cluster[i]
                if (len(to_compare2) > self.max_neighbors_to_check):
                    #take a subset
                    to_compare2 = list(to_compare2)\
                                   [:self.max_neighbors_to_check]

                for n1 in to_compare1:
                    if (dealbreaker):
                        break
                    for n2 in to_compare2:
                        if (cached_incompatibilities[n1,n2]):
                            dealbreaker = True
                            break 
                if (dealbreaker == False):
                    #if join, update all the neighbors
                    new_set = idx_to_others_in_cluster[i].union(
                               idx_to_others_in_cluster[j]) 
                    for an_idx in new_set:
                        idx_to_others_in_cluster[an_idx]=new_set 
                else:
                    #otherwise, update all the cached incompatibilities
                    for n1 in idx_to_others_in_cluster[i]:
                        for n2 in idx_to_others_in_cluster[j]:
                            cached_incompatibilities[n1, n2] = 1
                            cached_incompatibilities[n2, n1] = 1
                                
        distinct_sets = sorted(dict([(id(y), y) for y in 
                                idx_to_others_in_cluster.values()
                                if len(y) > self.min_cluster_size]).values(),
                               key=lambda x: -len(x))
        idx_to_cluster = {}
        for set_idx, the_set in enumerate(distinct_sets):
            for an_idx in the_set:
                idx_to_cluster[an_idx] = set_idx
        cluster_indices = np.array(
                    [idx_to_cluster[i] if i in idx_to_cluster
                     else -1 for i in range(len(affinity_mat))])

        return ClusterResults(cluster_indices=cluster_indices,
                    distinct_sets=distinct_sets)

