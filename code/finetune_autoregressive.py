import pickle
from datasets import Dataset
from transformers import AutoTokenizer, AutoModelWithLMHead, DataCollatorForLanguageModeling, TrainingArguments, Trainer
import math
import pandas as pd
import spacy
import sys

# ARGUMENTS
# shift +1 for deepspeed launcher
# sys.argv[2] pretrained model path
# sys.argv[3] dataset path (pickled pandas df)
# sys.argv[4] "twitter" or "news"
# sys.argv[5] custom save location for finetuned model (optional)
# sys.argv[6] model_max_length, defaults to 512

# define some helper functions

# sentence segmentation for news
nlp = spacy.load("en_core_web_sm")
def spacy_seg(row):
    doc = nlp(row["text"])
    return ([sent.text for sent in doc.sents])

MODEL_MAX_LENGTH = 512
# define tok function
def tokenize_function(examples):
    return tokenizer(examples["text"], truncation=True, max_length=MODEL_MAX_LENGTH)

# load dataset
print("loading dataset...")
df = pickle.load(open(sys.argv[3], 'rb'))

if sys.argv[4] in ["news", "News", "N", "n"]:
    # do sentence segmentation
   # print("Segmenting sentences...")
   # df['sentence'] = df.apply(lambda row: spacy_seg(row), axis=1)
    #df = df.explode("sentence")

    # remove unneeded columns
    df.drop(['stories_id', 'authors', 'publish_date', 'media_outlet', 'text'], axis=1, inplace=True)

    # deal with whitespace and empty strings - just in case
    df['sentence'] = df['sentence'].str.strip()
    df = df[df.sentence != '']
    df = df[df.sentence != None]
    df = df.dropna()
    
    # correct tok function
    def tokenize_function(examples):
        return tokenizer(examples["sentence"], truncation=True, max_length=MODEL_MAX_LENGTH)
    
elif sys.argv[4] in ["twitter", "Twitter", "Tweets", "tweets", "T", "t"]:
    # twitter normalization
    print("Normalizing tweets...")
    from TweetNormalizer import normalizeTweet
    df['normalized'] = df['text'].apply(normalizeTweet)

    # remove unneeded columns
    df.drop(['tweetid', 'userid', 'username', 'date', 'lang', 'location',
       'listed_count', 'following_count', 'statuses_count', 'verified',
       'display_name', 'account_creation_date', 'rt_text', 'qtd_text',
       'rp_text', 'tweet_type', 'ref_date', 'rt_userid', 'rt_username',
        'qt_userid', 'qt_username', 'rp_userid', 'rp_username', 'hashtag', 'text'], axis=1, inplace=True)

    # correct tok function
    def tokenize_function(examples):
        # have to specify max_length=None to get model max, which is what we want
        return tokenizer(examples["normalized"], truncation=True, max_length=MODEL_MAX_LENGTH)
    
# load into HF Datasets
dataset = Dataset.from_pandas(df)
datasets = dataset.train_test_split(test_size=.05)

# load pretrained model
pretrained_model = sys.argv[2] # path to pretrained model
model_name = pretrained_model.split('/')[-1]
tokenizer = AutoTokenizer.from_pretrained(pretrained_model, use_fast=True)
if tokenizer.pad_token == None: # for GPT2
    tokenizer.pad_token = tokenizer.eos_token

print("begin tokenizing...")
tokenized_datasets = datasets.map(tokenize_function, batched=True)

model = AutoModelWithLMHead.from_pretrained(pretrained_model)

# training arguments
training_args = TrainingArguments(
    f"{model_name}-finetuned-{sys.argv[3]}",
    evaluation_strategy = "steps",
    learning_rate=2e-5,
    weight_decay=0.01,
    push_to_hub=False,
    num_train_epochs = 1.0,
    fp16=True,
    deepspeed = "./ds_config_general.json",
    per_device_train_batch_size = 8,
    gradient_checkpointing = True, # for memory
    gradient_accumulation_steps = 10 # for speed
)

data_collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

# fine tune model
trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=tokenized_datasets["train"],
    eval_dataset=tokenized_datasets["test"],
    data_collator=data_collator,
)

print("Begin finetuning")
try:
    trainer.train(resume_from_checkpoint = True)
except ValueError:
    trainer.train()
    
# evaluate
eval_results = trainer.evaluate()
print(f"Perplexity: {math.exp(eval_results['eval_loss']):.2f}")

# construct a sensible name for a subdirectory of pwd
model.save_pretrained(f"{model_name}-finetuned-{sys.argv[3]}")
