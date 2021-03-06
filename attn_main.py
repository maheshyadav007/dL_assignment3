

'''
vocab_input_size = 29
vocan_tar_size = 66
max_input_len = 22
max_tar_len = 21
'''

from google.colab import drive
drive.mount('/content/gdrive')

TRAIN_PATH = "/content/gdrive/MyDrive/Datasets/dakshina_dataset_v1.0/hi/lexicons/hi.translit.sampled.train.tsv"
TEST_PATH = "/content/gdrive/MyDrive/Datasets/dakshina_dataset_v1.0/hi/lexicons/hi.translit.sampled.test.tsv"
VAL_PATH = "/content/gdrive/MyDrive/Datasets/dakshina_dataset_v1.0/hi/lexicons/hi.translit.sampled.dev.tsv"

!pip install wandb
import wandb

# Commented out IPython magic to ensure Python compatibility.
from __future__ import unicode_literals, print_function, division
from io import open
import unicodedata
import string
import re
import random
import numpy as np
import torch
import torch.nn as nn
from torch import optim
import torch.nn.functional as F
from torch.utils.data import Dataset, TensorDataset
from torch.utils.data import DataLoader
import matplotlib.pyplot as plt
#plt.switch_backend('agg')
import matplotlib.ticker as ticker
from IPython.display import HTML as html_print
from IPython.display import display
from matplotlib import font_manager as fm, rcParams
from matplotlib.font_manager import FontProperties
from types import SimpleNamespace
import plotly.express as px
import plotly.graph_objects as go

# %matplotlib inline


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

#Processing Data
SOS_token = 0
EOS_token = 1
class Lang:
    def __init__(self, name):
        self.name = name
        self.word2index = {}
        self.word2count = {}
        self.index2word = {0: "SOS", 1: "EOS"}
        self.n_words = 2  # Count SOS and EOS

    def addSentence(self, sentence):
        for word in sentence.split(' '):
            self.addWord(word)

    def addWord(self, word):
        if word not in self.word2index:
            self.word2index[word] = self.n_words
            self.word2count[word] = 1
            self.index2word[self.n_words] = word
            self.n_words += 1
        else:
            self.word2count[word] += 1

def unicodeToAscii(s):
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )

# Lowercase, trim, and remove non-letter characters


def normalizeString(s):
    s = unicodeToAscii(s.lower().strip())
    s = re.sub(r"([.!?])", r" \1", s)
    s = re.sub(r"[^a-zA-Z.!?]+", r" ", s)
    return s
def preprocess_sentence( w):        
        try:
          s = u""
          for l in w:
            s += l
            s+= " "
          w = s
        except:
          return w
        return w

def readLangs(path, lang1, lang2, reverse=False):
    lines = open(path , encoding='utf-8').\
        read().strip().split('\n')

    pairs = [[preprocess_sentence(s) for s in l.split('\t')] for l in lines]
    pairs = np.array(pairs)[:,0:2]
    if reverse:
        pairs = [list(reversed(p)) for p in pairs]
        input_lang = Lang(lang2)
        output_lang = Lang(lang1)
    else:
        input_lang = Lang(lang1)
        output_lang = Lang(lang2)
    return input_lang, output_lang, pairs

MAX_LENGTH = 22

def filterPair(p):
    return len(p[0].split(' ')) < MAX_LENGTH and \
        len(p[1].split(' ')) < MAX_LENGTH

def filterPairs(pairs):
    return [pair for pair in pairs if filterPair(pair)]

def prepareData(path, lang1, lang2, reverse=False):
    input_lang, output_lang, pairs = readLangs(path, lang1, lang2, reverse)
    pairs = filterPairs(pairs)
    for pair in pairs:
        input_lang.addSentence(pair[0])
        output_lang.addSentence(pair[1])
    #print(input_lang.name, input_lang.n_words)
    #print(output_lang.name, output_lang.n_words)
    return input_lang, output_lang, pairs


input_lang_train, output_lang_train, pairs_train = prepareData(TRAIN_PATH, 'hi', 'eng', True)
input_lang_val, output_lang_val, pairs_val = prepareData(VAL_PATH, 'hi', 'eng', True)
input_lang_test, output_lang_test, pairs_test = prepareData(TEST_PATH, 'hi', 'eng', True)

#print(random.choice(pairs))

#Enoder Decoder classes
class EncoderRNN(nn.Module):
    def __init__(self, input_size, hidden_size):
        super(EncoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.embedding = nn.Embedding(input_size, hidden_size)
        #self.gru = nn.GRU(hidden_size, hidden_size)
        self.lstm = nn.LSTM(hidden_size, hidden_size)
    def forward(self, input, hidden):
        embedded = self.embedding(input).view(1, 1, -1)
        output = embedded
        #output, hidden = self.gru(output, hidden)
        output, hidden = self.lstm(output,  (hidden[0], hidden[1]))
        return output, hidden

    def initHidden(self):
        return torch.zeros(1, 1, self.hidden_size, device=device)

class DecoderRNN(nn.Module):
    def __init__(self, hidden_size, output_size):
        super(DecoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.embedding = nn.Embedding(output_size, hidden_size)
        #self.gru = nn.GRU(hidden_size, hidden_size)
        self.lstm = nn.LSTM(hidden_size, (hidden_size, hidden_size))
        self.out = nn.Linear(hidden_size, output_size)
        self.softmax = nn.LogSoftmax(dim=1)

    def forward(self, input, hidden):
        output = self.embedding(input).view(1, 1, -1)
        output = F.relu(output)
        #output, hidden = self.gru(output, hidden)
        output, hidden = self.lstm(output, (hidden[0], hidden[1]))
        output = self.softmax(self.out(output[0]))
        return output, hidden

    def initHidden(self):
        return torch.zeros(1, 1, self.hidden_size, device=device)

class AttnDecoderRNN(nn.Module):
    def __init__(self, hidden_size, output_size, dropout_p=0.1, max_length=MAX_LENGTH):
        super(AttnDecoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.dropout_p = dropout_p
        self.max_length = max_length

        self.embedding = nn.Embedding(self.output_size, self.hidden_size)
        self.attn = nn.Linear(self.hidden_size * 2, self.max_length)
        self.attn_combine = nn.Linear(self.hidden_size * 2, self.hidden_size)
        self.dropout = nn.Dropout(self.dropout_p)
        #self.gru = nn.GRU(self.hidden_size, self.hidden_size)
        self.lstm = nn.LSTM(hidden_size, hidden_size)
        self.out = nn.Linear(self.hidden_size, self.output_size)

    def forward(self, input, hidden, encoder_outputs):
       
        embedded = self.embedding(input).view(1, 1, -1)
        embedded = self.dropout(embedded)
        
        attn_weights = F.softmax(self.attn(torch.cat((embedded[0], hidden[0][0]), 1)), dim=1)
        #print(attn_weights.shape, encoder_outputs.shape)
        attn_applied = torch.bmm(attn_weights.unsqueeze(0),encoder_outputs.unsqueeze(0))

        output = torch.cat((embedded[0], attn_applied[0]), 1)
        output = self.attn_combine(output).unsqueeze(0)

        output = F.relu(output)
        #output, hidden = self.gru(output, hidden)
        output, hidden = self.lstm(output, (hidden[0], hidden[1]))

        output =F.log_softmax(self.out(output[0]), dim=1)
        return output, hidden, attn_weights

    def initHidden(self):
        return torch.zeros(1, 1, self.hidden_size, device=device)

#Helper Functions for data processing 
def indexesFromSentence(lang, sentence):
    return [lang.word2index[word] for word in sentence.split(' ')]

def tensorFromSentence(lang, sentence):
    indexes = indexesFromSentence(lang, sentence)
    indexes.append(EOS_token)
    return torch.tensor(indexes, dtype=torch.long, device=device).view(-1, 1)

def tensorsFromPair(input_lang, output_lang, pair):
    input_tensor = tensorFromSentence(input_lang, pair[0])
    target_tensor = tensorFromSentence(output_lang, pair[1])
    return (input_tensor, target_tensor)

#Training Loop
teacher_forcing_ratio = 0.5

def train(input_tensor, target_tensor, encoder, decoder, encoder_optimizer, decoder_optimizer, criterion, max_length=MAX_LENGTH):
    encoder_hidden = encoder.initHidden()
    encoder_hidden = (encoder_hidden, encoder_hidden)
    encoder_optimizer.zero_grad()
    decoder_optimizer.zero_grad()
    input_length = input_tensor.size(0)
    target_length = target_tensor.size(0)

    encoder_outputs = torch.zeros(max_length, encoder.hidden_size, device=device)

    loss = 0

    for ei in range(input_length):
        encoder_output, encoder_hidden = encoder(
            input_tensor[ei], encoder_hidden)
        encoder_outputs[ei] = encoder_output[0, 0]

    decoder_input = torch.tensor([[SOS_token]], device=device)

    decoder_hidden = encoder_hidden

    #use_teacher_forcing = True if random.random() < teacher_forcing_ratio else False
    use_teacher_forcing = True
    if use_teacher_forcing:
        # Teacher forcing: Feed the target as the next input
        for di in range(target_length):
            decoder_output, decoder_hidden, decoder_attention = decoder(decoder_input, decoder_hidden, encoder_outputs)
            loss += criterion(decoder_output, target_tensor[di])
            decoder_input = target_tensor[di]  # Teacher forcing

    else:
        # Without teacher forcing: use its own predictions as the next input
        for di in range(target_length):
            decoder_output, decoder_hidden, decoder_attention = decoder(
                decoder_input, decoder_hidden, encoder_outputs)
            topv, topi = decoder_output.topk(1)
            decoder_input = topi.squeeze().detach()  # detach from history as input

            loss += criterion(decoder_output, target_tensor[di])
            if decoder_input.item() == EOS_token:
                break
    #loss = loss/(32*target_length)
    loss.backward()
    encoder_optimizer.step()
    decoder_optimizer.step()
    
    return loss.item() / target_length#loss, target_length

import time
import math
def asMinutes(s):
    m = math.floor(s / 60)
    s -= m * 60
    return '%dm %ds' % (m, s)


def timeSince(since, percent):
    now = time.time()
    s = now - since
    es = s / (percent)
    rs = es - s
    return '%s (- %s)' % (asMinutes(s), asMinutes(rs))

def trainIters(encoder, decoder, n_iters, print_every=1000, plot_every=100, learning_rate=0.01):
    start = time.time()
    plot_losses = []
    print_loss_total = 0  # Reset every print_every
    plot_loss_total = 0  # Reset every plot_every

    encoder_optimizer = optim.SGD(encoder.parameters(), lr=learning_rate)
    decoder_optimizer = optim.SGD(decoder.parameters(), lr=learning_rate)
    training_pairs = [tensorsFromPair(input_lang_train, output_lang_train, random.choice(pairs_train)) for i in range(n_iters)]
    criterion = nn.NLLLoss()
    for iter in range(1, n_iters + 1):
        training_pair = training_pairs[iter - 1]
        input_tensor = training_pair[0]
        target_tensor = training_pair[1]
        loss = train(input_tensor, target_tensor, encoder, decoder, encoder_optimizer, decoder_optimizer, criterion)
        print_loss_total += loss
        plot_loss_total += loss
        if iter % print_every == 0:
            
            print_loss_avg = print_loss_total / print_every
            print_loss_total = 0
            print('%s (%d %d%%) %.4f' % (timeSince(start, iter / n_iters),iter, iter / n_iters * 100, print_loss_avg ))
           
            train_acc = calc_accuracy(pairs_train, encoder, decoder,1000 )
            val_acc = calc_accuracy(pairs_val, encoder, decoder,1000 )
            print(train_acc, " ", val_acc)
            #wandb.log({'loss':print_loss_avg})
            #wandb.log({'Accuracy':train_acc})
            #wandb.log({'val_Accuracy':val_acc})
        if iter % plot_every == 0:
            plot_loss_avg = plot_loss_total / plot_every
            plot_losses.append(plot_loss_avg)
            plot_loss_total = 0

    #showPlot(plot_losses)

hyperparameter_defaults = dict(
    learning_rate = 0.01,
    latent_dims = 256,
    n_enc_layers = 1,
    n_dec_layers = 1,
    cell_type = "LSTM",
    dropout = .1,
    loss_func = "cross_entropy",
    )
sweep_config = {
    'name' : 'atttention sweep',
    "method": "random",
    'early_terminate':{
        'type': 'hyperband',
        'min_iter': 2,
        'eta' : 2
        },
    'metric': { 
        'name':'Accuracy',
        'goal': 'maximize',
        },
    'parameters':{
        'learning_rate': {'values' : [.01, 0.001]},
        'latent_dims' : {'values' : [32, 64, 256, 512]},
        'n_enc_layers' : {'values' : [1]},
        'n_dec_layers' : {'values' : [1]},
        'cell_type' : {'values' : ["LSTM"]},
        'dropout' : {'values' : [.1,.2,.3]},
        'loss_func':{'values' : [ 'cross_entropy']},
        }
}

#inderence method
def evaluate(encoder, decoder, sentence, max_length=MAX_LENGTH):
    with torch.no_grad():
        input_tensor = tensorFromSentence(input_lang_train, sentence)
        input_length = input_tensor.size()[0]
        encoder_hidden = encoder.initHidden()
        encoder_hidden = (encoder_hidden, encoder_hidden)
        encoder_outputs = torch.zeros(max_length, encoder.hidden_size, device=device)

        for ei in range(input_length):
            encoder_output, encoder_hidden = encoder(input_tensor[ei],
                                                     encoder_hidden)
            encoder_outputs[ei] += encoder_output[0, 0]

        decoder_input = torch.tensor([[SOS_token]], device=device)  # SOS

        decoder_hidden = encoder_hidden

        decoded_words = []
        decoder_attentions = torch.zeros(max_length, max_length)
        for di in range(max_length):
            #print(encoder_outputs.unsqueeze(0).shape)
            decoder_output, decoder_hidden, decoder_attention = decoder(decoder_input, decoder_hidden, encoder_outputs)
            decoder_attentions[di] = decoder_attention.data
            topv, topi = decoder_output.data.topk(1)
            if topi.item() == EOS_token:
                decoded_words.append('<EOS>')
                break
            else:
                decoded_words.append(output_lang_train.index2word[topi.item()])

            decoder_input = topi.squeeze().detach()
        #print(decoder_attentions.shape)
        return decoded_words, decoder_attentions[:di + 1]

#Helper functions to calc randomly and evaluation
def evaluateRandomly(pairs, encoder, decoder, n=10):
    for i in range(n):
        pair = random.choice(pairs)
        print('Input : ', "".join(pair[0]).replace(" ",""), end = " ")
        print('Expected : ', "".join(pair[1]).replace(" ",""), end = " ")
        output_words, attentions = evaluate(encoder, decoder, pair[0])
        output_sentence = ''.join(output_words[:-1])
        print('Predicted : ', output_sentence)
        
def calc_accuracy(pairs, encoder, decoder , n):
    c = 0
    for i in range(n):
        #pair = random.choice(pairs)
        output_words, _ = evaluate(encoder, decoder, pairs[i][0])

        output_sentence = ''.join(output_words[0:-2])
        #print(output_sentence,":", pairs[i][1].replace(" ",""),":")
        if output_sentence == pairs[i][1].replace(" ",""):
          c += 1
        
    print("Accuracy : ", c/n)

#Main function 
is_wandb_active = False
model = None
def run():
  #wandb.init(config=hyperparameter_defaults)
  #config = wandb.config
  
  config = SimpleNamespace(**hyperparameter_defaults)

  learning_rate = config.learning_rate
  n_enc_layers = config.n_enc_layers
  n_dec_layers = config.n_dec_layers
  latent_dims = config.latent_dims
  cell_type = config.cell_type
  dropout = config.dropout
  loss_func = config.loss_func

  embedding_size = latent_dims

  encoder1 = EncoderRNN(input_lang_train.n_words, latent_dims).to(device)
  attn_decoder1 = AttnDecoderRNN(latent_dims, output_lang_train.n_words, dropout_p=dropout).to(device)

  trainIters(encoder1, attn_decoder1, 120000, print_every=1000,learning_rate = learning_rate)

  if not is_wandb_active:
    return encoder1, attn_decoder1

if is_wandb_active:
  sweepId = wandb.sweep(sweep_config,entity = "dl_assignment3",project = "attn_rnn")
  wandb.agent(sweepId,function=run)
else:
  encoder1, attn_decoder1 = run()

#Calculate Accuracy
acc = calc_accuracy(pairs_test, encoder1, attn_decoder1,len(pairs_test) )
print(acc)

#Attention Heatmaps
i = 0
def showAttention(input_sentence, output_words, attentions):
    attentions = attentions.numpy()[:,6:-1]
    fig = go.Figure(data = go.Heatmap(z = attentions, y = input_sentence.split(" ")[:-1], x = output_words[:-2], type = 'heatmap',colorscale="viridis"))
    title = "Input: "+input_sentence.replace(" ","")+ "   Predicted: "+ "".join(output_words[:-1])
    fig.update_layout(title=title, autosize = False)#, autosize = False,width = 700, height = 700)
   
    #wandb.log({"chart"+ title : fig})

    fig.show()


def evaluateAndShowAttention(input_sentence):
    output_words, attentions = evaluate(encoder1, attn_decoder1, input_sentence)
    showAttention(input_sentence, output_words, attentions)

def evaluate_attn_randomly(pairs, encoder, decoder, n=10):
    for i in range(n):
        pair = random.choice(pairs)
        evaluateAndShowAttention(pair[0])
#wandb.init(project = "attn_rnn", entity = "dl_assignment3")
evaluate_attn_randomly(pairs_test, encoder1, attn_decoder1, 10)

#Visualizations
def cstr(s, color='black'):
	if s == ' ':
		return "<text style=color:#000;padding-left:10px;background-color:{}> </text>".format(color, s)
	else:
		return "<text style=color:#000;background-color:{}>{} </text>".format(color, s)

def print_color(t):
	display(html_print(''.join([cstr(ti, color=ci) for ti,ci in t])))

def get_clr(value):
  colors = ['#85c2e1', '#89c4e2', '#95cae5', '#99cce6', '#a1d0e8',
		'#b2d9ec', '#baddee', '#c2e1f0', '#eff7fb', '#f9e8e8',
		'#f9e8e8', '#f9d4d4', '#f9bdbd', '#f8a8a8', '#f68f8f',
		'#f47676', '#f45f5f', '#f34343', '#f33b3b', '#f42e2e']
  value = int((value * 100) / 5)
  if value >= 20:
    return colors[19]
  return colors[value]

def sigmoid(x):
	z = 1/(1 + np.exp(-x)) 
	return z

def visualize(output_values, result_list, inp_seq):
  inp_seq = inp_seq.split(" ")
  all_clrs = []
  for i in range(len(result_list[:-1])):
    clrs = []
    text_colours = []
    for j in range(len(output_values[i])) :
      clr = get_clr(output_values[i][j])
      if j >= len(inp_seq):
        break
      else:
        text = inp_seq[j]
      text = (text, clr)
      text_colours.append(text)
    print(result_list[i], end = "")
    print_color(text_colours)

def print_visualization(pair):
  output_word, attn = evaluate(encoder1, attn_decoder1, pair[0])
  attn = attn.numpy()[1:,3:-1]
  attn = np.array([sigmoid(a) for a in attn])
  print("Input:", "".join(pair[0]).replace(" ",""), end = " ")
  print("Expected:", "".join(pair[1]).replace(" ",""), end = " ")
  print("Predicted:", "".join(output_word[:-1]))
  visualize(attn, output_word, pair[0])

def visualize_randomly(pairs, encoder, decoder, n=10):
    for i in range(n):
        pair = random.choice(pairs)
        print_visualization(pair)
        print("")

visualize_randomly(pairs_test, encoder1, attn_decoder1, 10)

#Get prediction for all test data set
def get_prediction(pairs,encoder1, attn_decoder1):
  predicted = []
  actual = []
  inputs = []
  num_samples = len(pairs)
  for seq_index in range(0,num_samples):
      print('\r', seq_index, end = ' ')
      input_seq =  pairs[seq_index][0]
      decoded_sentence, _ = evaluate(encoder1, attn_decoder1,input_seq)
      #print(decoded_sentence)
      predicted.append("".join(decoded_sentence[:-2]))
      inputs.append( pairs[seq_index][0].replace(" ",""))
      actual.append( pairs[seq_index][1].replace(" ",""))
  return inputs, actual, predicted

inputs, actual, predicted = get_prediction(pairs_test, encoder1, attn_decoder1)

file1 = open('attn_predictions.txt', 'w')
file1.write("Input \t Actual Predicted \n")
for i in range(len(pairs_test)):
  file1.write(inputs[i])
  file1.write("\t")
  file1.write(actual[i])
  file1.write("\t")
  file1.write(predicted[i])
  file1.write("\n")
