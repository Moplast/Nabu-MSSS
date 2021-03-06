'''@file fracscorelabelperfeature_processor.py
contains the FracScorelabelperfeatureProcessor class'''


import os
import subprocess
import StringIO
import scipy.io.wavfile as wav
import numpy as np
import processor
from nabu.processing.feature_computers import feature_computer_factory
import pdb

class FracScorelabelperfeatureProcessor(processor.Processor):
    '''a processor for audio files, this will compute which bins and how much
    are used for scoring. This will be a number between 0 and 1'''

    def __init__(self, conf, segment_lengths):
        '''FracScorelabelperfeatureProcessor constructor

        Args:
            conf: FracScorelabelperfeatureProcessor configuration as a dict of strings
            segment_lengths: A list containing the desired lengths of segments. 
            Possibly multiple segment lengths'''

        #create the feature computer
        self.comp = feature_computer_factory.factory(conf['feature'])(conf)
        
        self.mag_low = float(conf['mag_low'])
        self.mag_high = float(conf['mag_high'])
        
        #set the length of the segments. Possibly multiple segment lengths
        self.segment_lengths = segment_lengths

        #initialize the metadata
        self.dim = self.comp.get_dim()
        self.nontime_dims=[self.dim]
        
        super(FracScorelabelperfeatureProcessor, self).__init__(conf)

    def __call__(self, dataline):
        '''process the data in dataline
        Args:
            dataline: either a path to a wav file or a command to read and pipe
                an audio file

        Returns:
            segmented_data: The segmented info on bins to be used for scoring as a list of numpy arrays per segment length
            utt_info: some info on the utterance'''
            
        utt_info= dict()

        #read the wav file
        rate, utt = _read_wav(dataline)

        #compute the features
        features = self.comp(utt, rate)
        
        #compute the floor
        maxbin = np.max(features)
	floor=maxbin/self.mag_low
	ceil=maxbin/self.mag_high
	    
	#apply floor to get the used bins
	usedbins=np.greater(features,floor,dtype=float)
	ceiledbins=np.less(features,ceil,dtype=float)
	frac_label=(features-floor)/(ceil-floor)*usedbins*ceiledbins+(1.0-ceiledbins)
        
        # split the data for all desired segment lengths
	segmented_data = self.segment_data(frac_label)

        return segmented_data, utt_info
      

    def write_metadata(self, datadir):
        '''write the processor metadata to disk

        Args:
            dir: the directory where the metadata should be written'''
            
        for i,seg_length in enumerate(self.segment_lengths):
	    seg_dir = os.path.join(datadir,seg_length)    
	    with open(os.path.join(seg_dir, 'dim'), 'w') as fid:
		fid.write(str(self.dim))
	    with open(os.path.join(seg_dir, 'nontime_dims'), 'w') as fid:
		fid.write(str(self.nontime_dims)[1:-1])

def _read_wav(wavfile):
    '''
    read a wav file

    Args:
        wavfile: either a path to a wav file or a command to read and pipe
            an audio file

    Returns:
        - the sampling rate
        - the utterance as a numpy array
    '''

    if os.path.exists(wavfile):
        #its a file
        (rate, utterance) = wav.read(wavfile)
    elif wavfile[-1] == '|':
        #its a command

        #read the audio file
        pid = subprocess.Popen(wavfile + ' tee', shell=True,
                               stdout=subprocess.PIPE)
        output, _ = pid.communicate()
        output_buffer = StringIO.StringIO(output)
        (rate, utterance) = wav.read(output_buffer)
    else:
        #its a segment of an utterance
        split = wavfile.split(' ')
        begin = float(split[-2])
        end = float(split[-1])
        unsegmented = ' '.join(split[:-2])
        rate, full_utterance = _read_wav(unsegmented)
        utterance = full_utterance[int(begin*rate):int(end*rate)]


    return rate, utterance
