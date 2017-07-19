import theano
from theano import tensor as T


def run_function_in_batches(func,
                            input_data_list,
                            learning_phase=None,
                            batch_size=10,
                            progress_update=1000,
                            multimodal_output=False):
    #func has a return value such that the first index is the
    #batch. This function will run func in batches on the inputData
    #and will extend the result into one big list.
    #if multimodal_output=True, func has a return value such that first
    #index is the mode and second index is the batch
    assert isinstance(input_data_list, list), "input_data_list must be a list"
    #input_datas is an array of the different input_data modes.
    to_return = [];
    i = 0;
    while i < len(input_data_list[0]):
        if (progress_update is not None):
            if (i%progress_update == 0):
                print("Done",i)
        func_output = func(*([x[i:i+batch_size] for x in input_data_list]
                                +([] if learning_phase is
                                   None else [learning_phase])
                        ))
        if (multimodal_output):
            assert isinstance(func_output, list),\
             "multimodal_output=True yet function return value is not a list"
            if (len(to_return)==0):
                to_return = [[] for x in func_output]
            for to_extend, batch_results in zip(to_return, func_output):
                to_extend.extend(batch_results)
        else:
            to_return.extend(func_output)
        i += batch_size;
    return to_return


def tensor_with_dims(num_dims, name):
    return T.TensorType(dtype=theano.config.floatX,
                        broadcastable=[False]*num_dims)(name)


def get_window_sum_function(window_size, same_size_return):
    """
        Returns a function for smoothening inputs with a window
         of size window_size.

        Returned function has arguments of inp,
         batch_size and progress_update
    """
    inp_tensor = tensor_with_dims(2, "inp_tensor") 
    inp_tensor = inp_tensor[:,None,None,:]

    if (same_size_return):
        border_mode='same'
    else:
        border_mode='valid'

    averaged_inp = theano.pool2d(
                        inp=inp_tensor,
                        pool_size=(1,window_size),
                        strides=(1,1),
                        border_mode=border_mode,
                        ignore_border=True,
                        pool_mode='avg_exc_pad') 

    #if window_size is even, then we have an extra value in the output,
    #so kick off the value from the front
    if (window_size%2==0 and same_size_return):
        averaged_inp = averaged_inp[:,:,:,1:]

    averaged_inp = averaged_inp[:,0,0,:]
    smoothen_func = theano.function([inp_tensor], averaged_inp*window_size)

    def smoothen(inp, batch_size, progress_update=None):
       return run_function_in_batches(
                func=smoothen_func,
                input_data_list=[inp],
                batch_size=batch_size,
                progress_update=progress_update)

    return smoothen


def get_argmax_function(): 
    inp_tensor = tensor_with_dims(2, "inp_tensor") 
    argmaxes = T.argmax(inp_tensor, axis=1) 
    argmax_func = theano.function([inp_tensor], argmaxes)
    def argmax_func(inp, batch_size, progress_update=None):
        return run_function_in_batches(
                func=argmax_func,
                input_data_list=[inp],
                batch_size=batch_size,
                progress_update=progress_update)
    return argmax_func


def get_max_cross_corr(filters, things_to_scan, min_overlap,
                       verbose=True, batch_size=50,
                       func_params_size=1000000,
                       progress_update=1000):
    """
        func_params_size: when compiling functions
    """
    #reverse the patterns as the func is a conv not a cross corr
    filters = filters.astype("float32")[:,::-1,::-1]
    to_return = np.zeros((filters.shape[0], len(things_to_scan)))
    #compile the number of filters that result in a function with
    #params equal to func_params_size 
    params_per_filter = np.prod(filters[0].shape)
    filter_batch_size = int(func_params_size/params_per_filter)
    filter_length = filters.shape[-1]
    filter_idx = 0 
    while filter_idx < filters.shape[0]:
        if (verbose):
            print("On filters",filter_idx,"to",(filter_idx+filter_batch_size))
        filter_batch = filters[filter_idx:(filter_idx+filter_batch_size)]

        cross_corr_func = compile_conv_func_with_theano(
                           set_of_2d_patterns_to_conv_with=filter_batch,
                           normalise_by_magnitude=False,
                           take_max=True)  

        padding_amount = int((filter_length)*(1-min_overlap))
        padded_input = [np.pad(array=x,
                              pad_width=((padding_amount, padding_amount)),
                              mode="constant") for x in things_to_scan]

        max_cross_corrs = np.array(deeplift.util.run_function_in_batches(
                            func=cross_corr_func,
                            input_data_list=[padded_input],
                            batch_size=batch_size,
                            progress_update=(None if verbose==False else
                                             progress_update)))
        assert len(max_cross_corrs.shape)==2, max_cross_corrs.shape
        to_return[filter_idx:
                  (filter_idx+filter_batch_size),:] =\
                  np.transpose(max_cross_corrs)
        filter_idx += filter_batch_size
        
    return to_return
