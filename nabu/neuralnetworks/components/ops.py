'''@file ops.py
some operations'''

import tensorflow as tf
import itertools
import pdb

def pyramid_stack(inputs, sequence_lengths, numsteps, axis=2, scope=None):
    '''
    concatenate each two consecutive elements

    Args:
        inputs: A time minor tensor [batch_size, time, input_size]
        sequence_lengths: the length of the input sequences
        numsteps: number of time steps to concatenate
        axis: the axis where the inputs should be stacked
        scope: the current scope

    Returns:
        inputs: Concatenated inputs
            [batch_size, time/numsteps, input_size*numsteps]
        sequence_lengths: the lengths of the inputs sequences [batch_size]
    '''

    with tf.name_scope(scope or 'pyramid_stack'):

        numdims = len(inputs.shape)

        #convert imputs to time major
        time_major_input = tf.transpose(inputs, [1, 0] + range(2, numdims))


        #pad the inputs to an appropriate length length
        length = tf.cast(tf.shape(time_major_input)[0], tf.float32)
        pad_length = tf.ceil(length/numsteps)*numsteps - length
        pad_length = tf.cast(pad_length, tf.int32)
        pad_shape = tf.concat([[pad_length],
                               tf.shape(time_major_input)[1:]], 0)
        padding = tf.zeros(pad_shape, dtype=inputs.dtype)
        padded_inputs = tf.concat([time_major_input, padding], 0)

        #get the new length
        length = tf.shape(padded_inputs)[0]

        #seperate the inputs for every concatenated timestep
        seperated = []
        for i in range(numsteps):
            seperated.append(tf.gather(
                padded_inputs, tf.range(i, length, numsteps)))

        #concatenate odd and even inputs
        time_major_outputs = tf.concat(seperated, axis)

        #convert back to time minor
        outputs = tf.transpose(time_major_outputs, [1, 0] + range(2, numdims))

        #compute the new sequence length
        output_sequence_lengths = tf.cast(tf.ceil(tf.cast(sequence_lengths,
                                                          tf.float32)/numsteps),
                                          tf.int32)

    return outputs

def seq2nonseq(sequential, sequence_lengths, name=None):
    '''
    Convert sequential data to non sequential data

    Args:
        sequential: the sequential data which is a [batch_size, max_length, dim]
            tensor
        sequence_lengths: a [batch_size] vector containing the sequence lengths
        name: [optional] the name of the operation

    Returns:
        non sequential data, which is a TxF tensor where T is the sum of all
        sequence lengths
    '''

    with tf.name_scope(name or 'seq2nonseq'):

        indices = get_indices(sequence_lengths)

        #create the values
        tensor = tf.gather_nd(sequential, indices)


    return tensor

def dense_sequence_to_sparse(sequences, sequence_lengths):
    '''convert sequence dense representations to sparse representations

    Args:
        sequences: the dense sequences as a [batch_size x max_length] tensor
        sequence_lengths: the sequence lengths as a [batch_size] vector

    Returns:
        the sparse tensor representation of the sequences
    '''

    with tf.name_scope('dense_sequence_to_sparse'):

        #get all the non padding sequences
        indices = tf.cast(get_indices(sequence_lengths), tf.int64)

        #create the values
        values = tf.gather_nd(sequences, indices)

        #the shape
        shape = tf.cast(tf.shape(sequences), tf.int64)

        sparse = tf.SparseTensor(indices, values, shape)

    return sparse

def L41_loss(targets, bin_embeddings, spk_embeddings, usedbins, seq_length, batch_size):
    '''
    Monaural Audio Speaker Separation Using Source-Contrastive Estimation
    Cory Stephenson, Patrick Callier, Abhinav Ganesh, and Karl Ni

    Args:
        targets: a [batch_size x time x (feat_dim*nrS)] tensor containing the binary targets
        bin_embeddings: a [batch_size x time x (feat_dim*emb_dim)] tensor containing 
        the timefrequency bin embeddings
        spk_embeddings: a [batch_size x 1 x (emb_dim*nrS))] tensor containing the speaker embeddings
        usedbins: a [batch_size x time x feat_dim] tensor indicating the bins to use in the loss function
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size

    Returns:
        a scalar value containing the loss
    '''

    with tf.name_scope('L41_loss'):
	feat_dim = tf.shape(usedbins)[2]
        output_dim = tf.shape(bin_embeddings)[2]
        emb_dim = output_dim/feat_dim
        target_dim = tf.shape(targets)[2]
        nrS = target_dim/feat_dim
                
        loss = 0.0
        norm = 0
        
        for utt_ind in range(batch_size):
	    N = seq_length[utt_ind]
	    usedbins_utt = usedbins[utt_ind]
	    usedbins_utt = usedbins_utt[:N,:]
	    bin_emb_utt = bin_embeddings[utt_ind]
	    bin_emb_utt = bin_emb_utt[:N,:]
	    targets_utt = targets[utt_ind]
	    targets_utt = targets_utt[:N,:]
	    spk_emb_utt = spk_embeddings[utt_ind]
	    
	    vi = tf.reshape(bin_emb_utt,[N,feat_dim,1,emb_dim],name='vi')
	    vi_norm = tf.nn.l2_normalize(vi,3,name='vi_norm')
	    vo = tf.reshape(spk_emb_utt,[1,1,nrS,emb_dim],name='vo')
	    vo_norm = tf.nn.l2_normalize(vo,3,name='vo_norm')
	    
	    dot = tf.reduce_sum(vi_norm*vo_norm,3,name='D')
	    
	    Y = tf.to_float(tf.reshape(targets_utt,[N,feat_dim,nrS]))
	    Y = (Y-0.5)*2.0
	    
	    # Compute the cost for every element
	    loss_utt = -tf.log(tf.nn.sigmoid(Y * dot))

	    loss_utt = tf.reduce_sum(tf.to_float(tf.expand_dims(usedbins_utt,-1))*loss_utt)
                
	    loss += loss_utt
	    
	    norm += tf.to_float(tf.reduce_sum(usedbins_utt)*nrS)
	    
    #loss = loss/tf.to_float(batch_size)
    
    return loss , norm
  
def pit_L41_loss(targets, bin_embeddings, spk_embeddings, mix_to_mask, seq_length, batch_size):
    '''
    Combination of L41 approach, where an attractor embedding per speaker is found and PIT 
    where the audio signals are reconstructed via mast estimation, which are used to define
    a loss in a permutation invariant way. Here the masks are estimated by evaluating the distance
    of a bin embedding to all speaker embeddings.

    Args:
        targets: a [batch_size x time x feat_dim  x nrS)] tensor containing the multiple targets
        bin_embeddings: a [batch_size x time x (feat_dim*emb_dim)] tensor containing 
        the timefrequency bin embeddings
        spk_embeddings: a [batch_size x 1 x (emb_dim*nrS)] tensor containing the speaker embeddings
        mix_to_mask: a [batch_size x time x feat_dim] tensor containing the mixture that will be masked
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size

    Returns:
        a scalar value containing the loss
    '''

    with tf.name_scope('PIT_L41_loss'):
	feat_dim = tf.shape(targets)[2]
        output_dim = tf.shape(bin_embeddings)[2]
        emb_dim = output_dim/feat_dim
        target_dim = tf.shape(targets)[2]
        nrS = targets.get_shape()[3]
        nrS_tf = tf.shape(targets)[3]
        permutations = list(itertools.permutations(range(nrS),nrS))
                
        loss = 0.0
        norm = tf.to_float(nrS_tf * feat_dim * tf.reduce_sum(seq_length))
        
        for utt_ind in range(batch_size):
	    N = seq_length[utt_ind]
	    bin_emb_utt = bin_embeddings[utt_ind]
	    bin_emb_utt = bin_emb_utt[:N,:]
	    targets_utt = targets[utt_ind]
	    targets_utt = targets_utt[:N,:,:]
	    spk_emb_utt = spk_embeddings[utt_ind]
	    mix_to_mask_utt = mix_to_mask[utt_ind]
	    mix_to_mask_utt = mix_to_mask_utt[:N,:]
	    
	    vi = tf.reshape(bin_emb_utt,[N,feat_dim,1,emb_dim],name='vi')
	    vi_norm = tf.nn.l2_normalize(vi,3,name='vi_norm')
	    vo = tf.reshape(spk_emb_utt,[1,1,nrS_tf,emb_dim],name='vo')
	    vo_norm = tf.nn.l2_normalize(vo,3,name='vo_norm')
	    
	    D = tf.divide(1,tf.norm(tf.subtract(vi_norm,vo_norm),ord=2,axis=3))
            Masks = tf.nn.softmax(D, axis=2)
	    
	    #The masks are estimated, the remainder is the same as in pit_loss
	    mix_to_mask_utt = tf.expand_dims(mix_to_mask_utt,-1)
	    recs = tf.multiply(Masks, mix_to_mask_utt)
	    
	    targets_resh = tf.transpose(targets_utt,perm=[2,0,1])
	    recs = tf.transpose(recs,perm=[2,0,1])
		               
	    perm_cost = []
	    for perm in permutations:
		tmp = tf.square(tf.norm(tf.gather(recs,perm)-targets_resh,ord='fro',axis=[1,2]))
		perm_cost.append(tf.reduce_sum(tmp))
		
	    loss_utt = tf.reduce_min(perm_cost)
	    
	    loss += loss_utt
	    
	    
    #loss = loss/tf.to_float(batch_size)
    
    return loss , norm

def intravar2centervar_rat_loss(targets, logits, usedbins, seq_length, batch_size):
    '''
    Not realy LDA. numerator is same as above (mean intra class variance), the denominator is the
    variance between the class means (e.g. for 2 classes this equals to the square of halve the distance 
    between the 2 means)

    Args:
        targets: a [batch_size x time x (feat_dim*nrS)] tensor containing the binary targets
        logits: a [batch_size x time x (feat_dim*emb_dim)] tensor containing the logits
        usedbins: a [batch_size x time x feat_dim] tensor indicating the bins to use in the loss function
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size

    Returns:
        a scalar value containing the loss
    '''
    print 'Using intravar2centervar_rat_loss'
    with tf.name_scope('intravar2centervar_rat_loss'):
	feat_dim = tf.shape(usedbins)[2]
        output_dim = tf.shape(logits)[2]
        emb_dim = output_dim/feat_dim
        target_dim = tf.shape(targets)[2]
        nrS = target_dim/feat_dim
                
        loss = 0.0
        norm = tf.constant(0.0)
        
        for utt_ind in range(batch_size):
	    N = seq_length[utt_ind]
	    Nspec = N*feat_dim
	    usedbins_utt = usedbins[utt_ind]
	    usedbins_utt = usedbins_utt[:N,:]
	    logits_utt = logits[utt_ind]
	    logits_utt = logits_utt[:N,:]
	    targets_utt = targets[utt_ind]
	    targets_utt = targets_utt[:N,:]
		               
	    ubresh=tf.cast(tf.reshape(usedbins_utt,[Nspec]),tf.bool,name='ubresh')
	    
	    V=tf.reshape(logits_utt,[Nspec,emb_dim]) 
	    V=tf.boolean_mask(V,ubresh,name='V')
	    Vnorm=tf.nn.l2_normalize(V, axis=1, epsilon=1e-12, name='Vnorm')
	    Y=tf.reshape(targets_utt,[Nspec,nrS])
	    Y=tf.boolean_mask(Y,ubresh,name='Y')
	    Y=tf.to_float(Y)
	    
	    YTY=tf.matmul(Y,Y,transpose_a=True)
	    Ycnt=tf.diag_part(YTY)
	    Ycnt=tf.expand_dims(Ycnt,-1)+1e-12
	    sum_s=tf.matmul(Y,Vnorm,transpose_a=True)
	    mean_s=tf.divide(sum_s,Ycnt)
	    VminYmean_S=Vnorm-tf.matmul(Y,mean_s)
	    dev=tf.reduce_sum(tf.square(VminYmean_S),1,keep_dims=True)
	    sum_dev_s=tf.matmul(Y,dev,transpose_a=True)
	    mean_dev_s=tf.divide(sum_dev_s,Ycnt)
	    intra_cluster_variance=tf.reduce_mean(mean_dev_s)
	    
	    _,inter_mean_var=tf.nn.moments(mean_s,0)
	    inter_mean_var=tf.reduce_sum(inter_mean_var)+1e-12

	    #if only 1 sample in a cluster, just return 1.0
	    loss_utt = tf.cond(tf.reduce_min(Ycnt) > 1.1, lambda: 
			  tf.divide(intra_cluster_variance,inter_mean_var), lambda: tf.constant(1.0))

	    loss += loss_utt
	    
	    norm += 1.0
    
    return loss , norm 

def dist2mean_epsilon_closest_rat_loss(targets, logits, usedbins, seq_length, batch_size,rat_power=1,
		       fracbins=None,epsilon=0.2):
    '''
    Not realy LDA. For each embedding determine the ratio of distance to its class center to distance to
    other closest class center

    Args:
        targets: a [batch_size x time x (feat_dim*nrS)] tensor containing the binary targets
        logits: a [batch_size x time x (feat_dim*emb_dim)] tensor containing the logits
        usedbins: a [batch_size x time x feat_dim] tensor indicating the bins to use in the loss function
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size
        rat_power: the loss is the ratio to the power of rat_power
        fracbins: a [batch_size x time x feat_dim] tensor, similar to usedbins, but allowing
	    for a gradual assigment between 0 and 1

    Returns:
        a scalar value containing the loss
    '''
    print 'Using dist2mean_closest_rat_loss'
    with tf.name_scope('dist2mean_closest_rat_loss'):
	feat_dim = usedbins.get_shape()[2]
        output_dim = logits.get_shape()[2]
        emb_dim = output_dim/feat_dim
        target_dim = targets.get_shape()[2]
        nrS = target_dim/feat_dim
        
                
	ubresh=tf.to_float(tf.reshape(usedbins,[batch_size,-1],name='ubresh') )
	ubresh_expand=tf.expand_dims(ubresh,-1)
        V=tf.reshape(logits,[batch_size,-1,emb_dim])
        V=tf.multiply(V,ubresh_expand,name='V')
        Vnorm=tf.nn.l2_normalize(V, axis=2, epsilon=1e-12, name='Vnorm')
        Y=tf.reshape(targets,[batch_size,-1,nrS])
	Y=tf.to_float(Y)
	Y=tf.multiply(Y,ubresh_expand,name='Y')
                
        Ycnt=tf.expand_dims(tf.reduce_sum(Y,1),-1)+1e-12
	sum_s=tf.matmul(tf.transpose(Y,[0,2,1]),Vnorm)
	mean_s=tf.divide(sum_s,Ycnt)
	mean_s_resh=tf.expand_dims(tf.transpose(mean_s,[0,2,1]),1)
	Vnorm_resh=tf.expand_dims(Vnorm,-1)
	dev=tf.reduce_sum(tf.square(Vnorm_resh-mean_s_resh),2)
	rat=tf.reduce_sum(dev*Y,2)/(tf.reduce_min(dev+Y*1e20,2)+epsilon)
	rat=rat*ubresh
	
	if rat_power==2:
	  rat=tf.square(rat)
	elif rat_power!=1:
	  rat=rat**rat_power
	  
	if fracbins!=None:
	    fracbins_resh = tf.reshape(fracbins,[batch_size, -1])
	    fracbins_act=tf.multiply(fracbins_resh,ubresh)
	    rat*=fracbins_act
	    
	loss= tf.reduce_sum(rat)
	
	if fracbins==None:
	    norm = tf.to_float(tf.reduce_sum(usedbins))
	else:
	    norm = tf.reduce_sum(fracbins_act)
        
    return loss , norm

def dist2mean_closest_rat_loss(targets, logits, usedbins, seq_length, batch_size,rat_power=1,
		       fracbins=None):
    '''
    Not realy LDA. For each embedding determine the ratio of distance to its class center to distance to
    other closest class center

    Args:
        targets: a [batch_size x time x (feat_dim*nrS)] tensor containing the binary targets
        logits: a [batch_size x time x (feat_dim*emb_dim)] tensor containing the logits
        usedbins: a [batch_size x time x feat_dim] tensor indicating the bins to use in the loss function
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size
        rat_power: the loss is the ratio to the power of rat_power
        fracbins: a [batch_size x time x feat_dim] tensor, similar to usedbins, but allowing
	    for a gradual assigment between 0 and 1

    Returns:
        a scalar value containing the loss
    '''
    print 'Using dist2mean_closest_rat_loss'
    with tf.name_scope('dist2mean_closest_rat_loss'):
	feat_dim = usedbins.get_shape()[2]
        output_dim = logits.get_shape()[2]
        emb_dim = output_dim/feat_dim
        target_dim = targets.get_shape()[2]
        nrS = target_dim/feat_dim
        
                
	ubresh=tf.to_float(tf.reshape(usedbins,[batch_size,-1],name='ubresh') )
	ubresh_expand=tf.expand_dims(ubresh,-1)
        V=tf.reshape(logits,[batch_size,-1,emb_dim])
        V=tf.multiply(V,ubresh_expand,name='V')
        Vnorm=tf.nn.l2_normalize(V, axis=2, epsilon=1e-12, name='Vnorm')
        Y=tf.reshape(targets,[batch_size,-1,nrS])
	Y=tf.to_float(Y)
	Y=tf.multiply(Y,ubresh_expand,name='Y')
                
        Ycnt=tf.expand_dims(tf.reduce_sum(Y,1),-1)+1e-12
	sum_s=tf.matmul(tf.transpose(Y,[0,2,1]),Vnorm)
	mean_s=tf.divide(sum_s,Ycnt)
	mean_s_resh=tf.expand_dims(tf.transpose(mean_s,[0,2,1]),1)
	Vnorm_resh=tf.expand_dims(Vnorm,-1)
	dev=tf.reduce_sum(tf.square(Vnorm_resh-mean_s_resh),2)
	rat=tf.reduce_sum(dev*Y,2)/(tf.reduce_min(dev+Y*1e20,2)+1.0e-12)
	rat=rat*ubresh
	
	if rat_power==2:
	  rat=tf.square(rat)
	elif rat_power!=1:
	  rat=rat**rat_power
	  
	if fracbins!=None:
	    fracbins_resh = tf.reshape(fracbins,[batch_size, -1])
	    fracbins_act=tf.multiply(fracbins_resh,ubresh)
	    rat*=fracbins_act
	    
	loss= tf.reduce_sum(rat)
	
	if fracbins==None:
	    norm = tf.to_float(tf.reduce_sum(usedbins))
	else:
	    norm = tf.reduce_sum(fracbins_act)
        
    return loss , norm
  
def dist2mean_rat_loss(targets, logits, usedbins, seq_length, batch_size,rat_power=1,
		       fracbins=None):
    '''
    Not realy LDA. For each embedding determine the ratio of distance to its class center to distance to
    other class centers

    Args:
        targets: a [batch_size x time x (feat_dim*nrS)] tensor containing the binary targets
        logits: a [batch_size x time x (feat_dim*emb_dim)] tensor containing the logits
        usedbins: a [batch_size x time x feat_dim] tensor indicating the bins to use in the loss function
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size
        rat_power: the loss is the ratio to the power of rat_power
        fracbins: a [batch_size x time x feat_dim] tensor, similar to usedbins, but allowing
	    for a gradual assigment between 0 and 1

    Returns:
        a scalar value containing the loss
    '''
    print 'Using dist2mean_rat_loss'
    with tf.name_scope('dist2mean_rat_loss'):
	feat_dim = usedbins.get_shape()[2]
        output_dim = logits.get_shape()[2]
        emb_dim = output_dim/feat_dim
        target_dim = targets.get_shape()[2]
        nrS = target_dim/feat_dim
        
                
	ubresh=tf.to_float(tf.reshape(usedbins,[batch_size,-1],name='ubresh') )
	ubresh_expand=tf.expand_dims(ubresh,-1)
        V=tf.reshape(logits,[batch_size,-1,emb_dim])
        V=tf.multiply(V,ubresh_expand,name='V')
        Vnorm=tf.nn.l2_normalize(V, axis=2, epsilon=1e-12, name='Vnorm')
        Y=tf.reshape(targets,[batch_size,-1,nrS])
	Y=tf.to_float(Y)
	Y=tf.multiply(Y,ubresh_expand,name='Y')
                
        Ycnt=tf.expand_dims(tf.reduce_sum(Y,1),-1)+1e-12
	sum_s=tf.matmul(tf.transpose(Y,[0,2,1]),Vnorm)
	mean_s=tf.divide(sum_s,Ycnt)
	mean_s_resh=tf.expand_dims(tf.transpose(mean_s,[0,2,1]),1)
	Vnorm_resh=tf.expand_dims(Vnorm,-1)
	dev=tf.reduce_sum(tf.square(Vnorm_resh-mean_s_resh),2)
	rat=tf.reduce_sum(dev*Y,2)/(tf.reduce_sum(dev*(1.0-Y),2)+1e-12)
	rat=rat*ubresh
	
	if rat_power==2:
	  rat=tf.square(rat)
	elif rat_power!=1:
	  rat=rat**rat_power
	  
	if fracbins!=None:
	    fracbins_resh = tf.reshape(fracbins,[batch_size, -1])
	    fracbins_act=tf.multiply(fracbins_resh,ubresh)
	    rat*=fracbins_act
	    
	loss= tf.reduce_sum(rat)
	
	if fracbins==None:
	    norm = tf.to_float(tf.reduce_sum(usedbins))
	else:
	    norm = tf.reduce_sum(fracbins_act)
        
    return loss , norm
  
  
def dist2mean_rat_loss(targets, logits, usedbins, seq_length, batch_size,rat_power=1,
		       fracbins=None):
    '''
    Duplicate of LdaJer3_loss. Remove LdaJer3_loss
    Not realy LDA. For each embedding determine the ratio of distance to its class center to distance to
    other class centers

    Args:
        targets: a [batch_size x time x (feat_dim*nrS)] tensor containing the binary targets
        logits: a [batch_size x time x (feat_dim*emb_dim)] tensor containing the logits
        usedbins: a [batch_size x time x feat_dim] tensor indicating the bins to use in the loss function
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size
        rat_power: the loss is the ratio to the power of rat_power
        fracbins: a [batch_size x time x feat_dim] tensor, similar to usedbins, but allowing
	    for a gradual assigment between 0 and 1

    Returns:
        a scalar value containing the loss
    '''
    print 'Using dist2mean_rat_loss'
    with tf.name_scope('dist2mean_rat_loss'):
	feat_dim = tf.shape(usedbins)[2]
        output_dim = tf.shape(logits)[2]
        emb_dim = output_dim/feat_dim
        target_dim = tf.shape(targets)[2]
        nrS = target_dim/feat_dim
                
        loss = 0.0
        norm = tf.constant(0.0)
        
        for utt_ind in range(batch_size):
	    N = seq_length[utt_ind]
	    Nspec = N*feat_dim
	    usedbins_utt = usedbins[utt_ind]
	    usedbins_utt = usedbins_utt[:N,:]
	    logits_utt = logits[utt_ind]
	    logits_utt = logits_utt[:N,:]
	    targets_utt = targets[utt_ind]
	    targets_utt = targets_utt[:N,:]
		               
	    ubresh=tf.cast(tf.reshape(usedbins_utt,[-1]),tf.bool,name='ubresh')
	    
	    V=tf.reshape(logits_utt,[Nspec,emb_dim]) 
	    V=tf.boolean_mask(V,ubresh,name='V')
	    Vnorm=tf.nn.l2_normalize(V, axis=1, epsilon=1e-12, name='Vnorm')
	    Y=tf.reshape(targets_utt,[Nspec,nrS])
	    Y=tf.boolean_mask(Y,ubresh,name='Y')
	    Y=tf.to_float(Y)
	    
	    YTY=tf.matmul(Y,Y,transpose_a=True)
	    Ycnt=tf.diag_part(YTY)
	    Ycnt=tf.expand_dims(Ycnt,-1)+1e-12
	    sum_s=tf.matmul(Y,Vnorm,transpose_a=True)
	    mean_s=tf.divide(sum_s,Ycnt)
	    mean_s_resh=tf.expand_dims(tf.transpose(mean_s),0)
	    Vnorm_resh=tf.expand_dims(Vnorm,-1)
	    dev=tf.reduce_sum(tf.square(Vnorm_resh-mean_s_resh),1)
	    rat=tf.reduce_sum(dev*Y,1)/tf.reduce_sum(dev*(1.0-Y),1)
	    
	    if rat_power==2:
	      rat=tf.square(rat)
	    elif rat_power!=1:
	      rat=rat**rat_power
	      
	    if fracbins!=None:
		fracbins_utt = fracbins[utt_ind]
		fracbins_utt = fracbins_utt[:N]
		fracbins_utt_resh = tf.reshape(fracbins_utt_active,[-1])
		fracbins_utt_act=tf.boolean_mask(fracbins_utt_resh,ubresh)
		rat*=fracbins_utt_act
	      
	      
	    loss_utt=tf.reduce_sum(rat)

	    loss += loss_utt
	    
	    if fracbins==None:
		norm += tf.to_float(tf.reduce_sum(usedbins_utt))
	    else:
		norm += tf.reduce_sum(fracbins_utt_act)
    
    return loss , norm  
  
def deepclustering_loss(targets, logits, usedbins, seq_length, batch_size):
    '''
    Compute the deep clustering loss
    cost function based on Hershey et al. 2016

    Args:
        targets: a [batch_size x time x (feat_dim*nrS)] tensor containing the binary targets
        logits: a [batch_size x time x (feat_dim*emb_dim)] tensor containing the logits
        usedbins: a [batch_size x time x feat_dim] tensor indicating the bins to use in the loss function
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size

    Returns:
        a scalar value containing the loss
    '''
    
    with tf.name_scope('deepclustering_loss'):
	feat_dim = usedbins.get_shape()[2]
        output_dim = logits.get_shape()[2]
        emb_dim = output_dim/feat_dim
        target_dim = targets.get_shape()[2]
        nrS = target_dim/feat_dim
                
	ubresh=tf.reshape(usedbins,[batch_size,-1,1],name='ubresh') 
	ubresh=tf.to_float(ubresh)
        
        V=tf.reshape(logits,[batch_size,-1,emb_dim],name='V')
        Vnorm=tf.nn.l2_normalize(V, axis=2, epsilon=1e-12, name='Vnorm')
        Vnorm=tf.multiply(Vnorm,ubresh)
        Y=tf.reshape(targets,[batch_size,-1,nrS],name='Y')
	Y=tf.to_float(Y)
	Y=tf.multiply(Y,ubresh)
	
	prod1=tf.matmul(Vnorm,Vnorm,transpose_a=True, transpose_b=False, name='VTV')
        prod2=tf.matmul(Vnorm,Y,transpose_a=True, transpose_b=False, name='VTY')
        prod3=tf.matmul(Y,Y,transpose_a=True, transpose_b=False, name='YTY')
        term1=tf.reduce_sum(tf.square(prod1),name='frob_1')
	term2=tf.reduce_sum(tf.square(prod2),name='frob_2')
	term3=tf.reduce_sum(tf.square(prod3),name='frob_3')
	
	term1and2=tf.add(term1,-2*term2,name='term1and2')
	loss=tf.add(term1and2,term3,name='term1and2and3')
	norm= tf.reduce_sum(tf.square(tf.to_float(tf.reduce_sum(usedbins,[1,2]))))

    
    return loss , norm  
  
def dc_pit_loss(targets_dc, logits_dc, targets_pit, logits_pit, usedbins, mix_to_mask, seq_length, batch_size,alpha=1.423024812840571e-09):
    '''
    THIS IS OBSOLETE. JUST COMBINE THE 2 LOSSES IN A LOSS COMPUTER
    Compute the joint deep clustering loss and permuation invariant loss
    cost function based on Hershey et al. 2016

    Args:
        targets_dc: a [batch_size x time x (feat_dim*nrS)] tensor containing the binary targets
        logits_dc: a [batch_size x time x (feat_dim*emb_dim)] tensor containing the logits
        usedbins: a [batch_size x time x feat_dim] tensor indicating the bins to use in the loss function
        targets_pit: a [batch_size x time x feat_dim  x nrS)] tensor containing the multiple targets
        logits_pit: a [batch_size x time x (feat_dim*nrS)] tensor containing the logits for pit
        mix_to_mask: a [batch_size x time x feat_dim] tensor containing the mixture that will be masked
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size
        alpha: PIT scaling loss

    Returns:
        a scalar value containing the loss
    '''
    
    #rougly estimated loss scaling factor so PIT loss and DC loss are more or less of the same magnitude
    
    with tf.name_scope('dc_pit_loss'):
	feat_dim = usedbins.get_shape()[2]
        output_dc_dim = logits_dc.get_shape()[2]
        emb_dim = output_dc_dim/feat_dim
        target_dc_dim = targets_dc.get_shape()[2]
        output_pit_dim = logits_pit.get_shape()[2]
        nrS = targets_pit.get_shape()[3]
        permutations = list(itertools.permutations(range(nrS),nrS))
        
        #DC
	ubresh=tf.reshape(usedbins,[batch_size,-1,1],name='ubresh') 
	ubresh=tf.to_float(ubresh)
        
        V=tf.reshape(logits_dc,[batch_size,-1,emb_dim],name='V')
        Vnorm=tf.nn.l2_normalize(V, axis=2, epsilon=1e-12, name='Vnorm')
        Vnorm=tf.multiply(Vnorm,ubresh)
        Y=tf.reshape(targets_dc,[batch_size,-1,nrS],name='Y')
	Y=tf.to_float(Y)
	Y=tf.multiply(Y,ubresh)
	
	prod1=tf.matmul(Vnorm,Vnorm,transpose_a=True, transpose_b=False, name='VTV')
        prod2=tf.matmul(Vnorm,Y,transpose_a=True, transpose_b=False, name='VTY')
        prod3=tf.matmul(Y,Y,transpose_a=True, transpose_b=False, name='YTY')
        term1=tf.reduce_sum(tf.square(prod1),name='frob_1')
	term2=tf.reduce_sum(tf.square(prod2),name='frob_2')
	term3=tf.reduce_sum(tf.square(prod3),name='frob_3')
	
	term1and2=tf.add(term1,-2*term2,name='term1and2')
	loss_dc=tf.add(term1and2,term3,name='term1and2and3')
	norm_dc= tf.reduce_sum(tf.square(tf.to_float(tf.reduce_sum(usedbins,[1,2]))))
	
	#PIT
	logits_pit_resh = tf.transpose(tf.reshape(tf.transpose(logits_pit,[2,0,1]),[nrS,feat_dim,batch_size,-1]),[2,3,1,0])
        Masks = tf.nn.softmax(logits_pit_resh, axis=3)
        
        mix_to_mask = tf.expand_dims(mix_to_mask,-1)
        recs = tf.multiply(Masks, mix_to_mask)
	    
	targets_pit_resh = tf.transpose(targets_pit,perm=[3,0,1,2])
	recs = tf.transpose(recs,perm=[3,0,1,2])
                
        perm_cost = []
	for perm in permutations:
	    tmp = tf.square(tf.norm(tf.gather(recs,perm)-targets_pit_resh,ord='fro',axis=[2,3]))
	    perm_cost.append(tf.reduce_sum(tmp,0))
	    
	loss_pit_utt = tf.reduce_min(perm_cost,0)
	loss_pit=tf.reduce_sum(loss_pit_utt)
        norm_pit = tf.to_float(tf.reduce_sum(seq_length)*nrS * feat_dim )

	loss=loss_dc/norm_dc+alpha*loss_pit/norm_pit
	norm=tf.constant(1.0)
    
    return loss , norm  

  
def deepclustering_L1_loss(targets, logits, usedbins, seq_length, batch_size):
    '''
    Compute the deep clustering loss, with L1 norm (instead of frobenius)
    cost function based on Hershey et al. 2016

    Args:
        targets: a [batch_size x time x (feat_dim*nrS)] tensor containing the binary targets
        logits: a [batch_size x time x (feat_dim*emb_dim)] tensor containing the logits
        usedbins: a [batch_size x time x feat_dim] tensor indicating the bins to use in the loss function
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size

    Returns:
        a scalar value containing the loss
    '''

    with tf.name_scope('deepclustering_loss'):
	feat_dim = tf.shape(usedbins)[2]
        output_dim = tf.shape(logits)[2]
        emb_dim = output_dim/feat_dim
        target_dim = tf.shape(targets)[2]
        nrS = target_dim/feat_dim
                
        loss = 0.0
        norm = 0.0
        
        for utt_ind in range(batch_size):
	    N = seq_length[utt_ind]
	    Nspec = N*feat_dim
	    usedbins_utt = usedbins[utt_ind]
	    usedbins_utt = usedbins_utt[:N,:]
	    logits_utt = logits[utt_ind]
	    logits_utt = logits_utt[:N,:]
	    targets_utt = targets[utt_ind]
	    targets_utt = targets_utt[:N,:]
		               
	    #remove the non_silence (cfr bins below energy thresh) bins. Removing in logits and
	    #targets will give 0 contribution to loss.
	    ubresh=tf.reshape(usedbins_utt,[Nspec,1],name='ubresh')
	    ubreshV=tf.tile(ubresh,[1,emb_dim])
	    ubreshV=tf.to_float(ubreshV)
	    ubreshY=tf.tile(ubresh,[1,nrS])
	    
	    V=tf.reshape(logits_utt,[Nspec,emb_dim],name='V') 
	    Vnorm=tf.nn.l2_normalize(V, axis=1, epsilon=1e-12, name='Vnorm')
	    Vnorm=tf.multiply(Vnorm,ubreshV)
	    Y=tf.reshape(targets_utt,[Nspec,nrS],name='Y')
	    Y=tf.multiply(Y,ubreshY)
	    Y=tf.to_float(Y)

	    prod1=tf.matmul(Vnorm,Vnorm,transpose_a=True, transpose_b=False, a_is_sparse=True, 
			    b_is_sparse=True, name='VTV')
	    prod2=tf.matmul(Vnorm,Y,transpose_a=True, transpose_b=False, a_is_sparse=True, 
			    b_is_sparse=True, name='VTY')
	    
	    term1=tf.reduce_sum(tf.abs(prod1),name='L1_1')
	    term2=tf.reduce_sum(tf.abs(prod2),name='L1_2')
	    
	    loss_utt = tf.add(term1,-2*term2,name='term1and2')
	    #normalizer= tf.to_float(tf.square(tf.reduce_sum(ubresh)))
	    #loss += loss_utt/normalizer*(10**9)
	    loss += loss_utt
	    
	    norm += tf.square(tf.to_float(tf.reduce_sum(usedbins_utt)))
	    
    #loss = loss/tf.to_float(batch_size)
    
    return loss , norm 
  
def crossentropy_multi_loss(labels, logits, batch_size):
  
    with tf.name_scope('crossentropy_multi_loss'):
	nrS = logits.get_shape()[1]
	permutations = list(itertools.permutations(range(nrS),nrS))
	
	perm_cost = []
	for perm in permutations:
	  logits_resh=tf.gather(logits,perm,axis=1)
	  tmp=tf.nn.sparse_softmax_cross_entropy_with_logits(labels=labels,logits=logits_resh)
	  perm_cost.append(tf.reduce_mean(tmp,-1))
	  
	loss= tf.reduce_sum(tf.reduce_min(perm_cost,0))
	norm=tf.to_float(batch_size)
	
    return loss, norm

def direct_loss(targets, logits, mix_to_mask, seq_length, batch_size):
    '''
    Compute the direct reconstruction loss via masks.

    Args:
        targets: a [batch_size x time x feat_dim  x nrS)] tensor containing the multiple targets
        logits: a [batch_size x time x (feat_dim*nrS)] tensor containing the logits
        mix_to_mask: a [batch_size x time x feat_dim] tensor containing the mixture that will be masked
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size

    Returns:
        a scalar value containing the loss
    '''
    with tf.name_scope('direct_loss'):
	feat_dim = targets.get_shape()[2]
        output_dim = logits.get_shape()[2]
        nrS = targets.get_shape()[3]
      
        logits_resh = tf.transpose(tf.reshape(tf.transpose(logits,[2,0,1]),[nrS,feat_dim,batch_size,-1]),[2,3,1,0])
        Masks = tf.nn.softmax(logits_resh, axis=3)
        
        mix_to_mask = tf.expand_dims(mix_to_mask,-1)
        recs = tf.multiply(Masks, mix_to_mask)
        
        norm = tf.to_float(tf.reduce_sum(seq_length)*nrS * feat_dim )
	    
	loss_utt = tf.square(tf.norm(recs-targets,ord='fro',axis=[1,2]))
	loss=tf.reduce_sum(loss_utt)
	        
    return loss, norm
  
def pit_loss(targets, logits, mix_to_mask, seq_length, batch_size):
    '''
    Compute the permutation invariant loss.
    Remark: This is implementation is different from pit_loss as the last dimension of logits is 
    still feat_dim*nrS, but the first feat_dim entries correspond to the first speaker and the
    second feat_dim entries correspond to the second speaker and so on. In pit_loss, the first nrS
    entries corresponded to the first feature dimension, the second nrS entries to the seocnd 
    feature dimension and so on.
    Remark2: There is actually a more efficient approach to calculate this loss. First calculate
    the loss for every reconstruction to every target ==> nrS^2 combinations and then add 
    together the losses to form every possible permutation.

    Args:
        targets: a [batch_size x time x feat_dim  x nrS)] tensor containing the multiple targets
        logits: a [batch_size x time x (feat_dim*nrS)] tensor containing the logits
        mix_to_mask: a [batch_size x time x feat_dim] tensor containing the mixture that will be masked
        seq_length: a [batch_size] vector containing the
            sequence lengths
        batch_size: the batch size

    Returns:
        a scalar value containing the loss
    '''
    
    with tf.name_scope('PIT_loss'):
	feat_dim = targets.get_shape()[2]
        output_dim = logits.get_shape()[2]
        nrS = targets.get_shape()[3]
        permutations = list(itertools.permutations(range(nrS),nrS))
      
        logits_resh = tf.transpose(tf.reshape(tf.transpose(logits,[2,0,1]),[nrS,feat_dim,batch_size,-1]),[2,3,1,0])
        Masks = tf.nn.softmax(logits_resh, axis=3)
        
        mix_to_mask = tf.expand_dims(mix_to_mask,-1)
        recs = tf.multiply(Masks, mix_to_mask)
	    
	targets_resh = tf.transpose(targets,perm=[3,0,1,2])
	recs = tf.transpose(recs,perm=[3,0,1,2])
                
        norm = tf.to_float(tf.reduce_sum(seq_length)*nrS * feat_dim )
        
        perm_cost = []
	for perm in permutations:
	    tmp = tf.square(tf.norm(tf.gather(recs,perm)-targets_resh,ord='fro',axis=[2,3]))
	    perm_cost.append(tf.reduce_sum(tmp,0))
	    
	loss_utt = tf.reduce_min(perm_cost,0)
	loss=tf.reduce_sum(loss_utt)
	        
    return loss, norm
  
def cross_entropy_loss_eos(targets, logits, logit_seq_length,
                           target_seq_length):
    '''
    Compute the cross_entropy loss with an added end of sequence label

    Args:
        targets: a [batch_size x time] tensor containing the targets
        logits: a [batch_size x time x num_classes] tensor containing the logits
        logit_seq_length: a [batch_size] vector containing the
            logit sequence lengths
        target_seq_length: a [batch_size] vector containing the
            target sequence lengths

    Returns:
        a scalar value containing the loss
    '''

    batch_size = tf.shape(targets)[0]

    with tf.name_scope('cross_entropy_loss'):

        output_dim = tf.shape(logits)[2]

        #get the logits for the final timestep
        indices = tf.stack([tf.range(batch_size),
                            logit_seq_length-1],
                           axis=1)
        final_logits = tf.gather_nd(logits, indices)

        #stack all the logits except the final logits
        stacked_logits = seq2nonseq(logits,
                                    logit_seq_length - 1)

        #create the stacked targets
        stacked_targets = seq2nonseq(targets,
                                     target_seq_length)

        #create the targets for the end of sequence labels
        final_targets = tf.tile([output_dim-1], [batch_size])

        #add the final logits and targets
        stacked_logits = tf.concat([stacked_logits, final_logits], 0)
        stacked_targets = tf.concat([stacked_targets, final_targets], 0)

        #compute the cross-entropy loss
        losses = tf.nn.sparse_softmax_cross_entropy_with_logits(
            logits=stacked_logits,
            labels=stacked_targets)

        loss = tf.reduce_mean(losses)

    return loss

def get_indices(sequence_length):
    '''get the indices corresponding to sequences (and not padding)

    Args:
        sequence_length: the sequence_lengths as a N-D tensor

    Returns:
        A [sum(sequence_length) x N-1] Tensor containing the indices'''

    with tf.name_scope('get_indices'):

        numdims = len(sequence_length.shape)

        #get th emaximal length
        max_length = tf.reduce_max(sequence_length)

        sizes = tf.shape(sequence_length)

        range_tensor = tf.range(max_length-1)
        for i in range(1, numdims):
            tile_dims = [1]*i + [sizes[i]]
            range_tensor = tf.tile(tf.expand_dims(range_tensor, i), tile_dims)

        indices = tf.where(tf.less(range_tensor,
                                   tf.expand_dims(sequence_length, numdims)))

    return indices

def mix(inputs, hidden_dim, scope=None):
    '''mix the layer in the time dimension'''

    with tf.variable_scope(scope or 'mix'):

        #append the possition to the inputs
        position = tf.expand_dims(tf.expand_dims(tf.range(
            tf.shape(inputs)[1]), 0), 2)
        position = tf.cast(position, tf.float32)
        position = tf.tile(position, [tf.shape(inputs)[0], 1, 1])
        expanded_inputs = tf.concat([inputs, position], 2)

        #apply the querry layer
        query = tf.contrib.layers.linear(expanded_inputs, hidden_dim,
                                         scope='query')

        #apply the attention layer
        queried = tf.contrib.layers.linear(expanded_inputs, hidden_dim,
                                           scope='queried')

        #create a sum for every combination of query and attention
        query = tf.expand_dims(query, 0)
        query = tf.tile(query, [tf.shape(query)[2], 1, 1, 1])
        summed = tf.transpose(tf.nn.tanh(query + queried), [1, 2, 0, 3])

        #map the combinations to single values
        attention = tf.contrib.layers.fully_connected(
            inputs=summed,
            num_outputs=1,
            scope='attention',
            activation_fn=tf.nn.tanh
        )[:, :, :, 0]

        #apply softmax to the attention values
        attention = tf.nn.softmax(attention)

        #use the attention to recombine the inputs
        outputs = tf.matmul(attention, inputs)

    return outputs
