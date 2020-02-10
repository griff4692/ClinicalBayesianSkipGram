# -*- coding: utf-8 -*-
"""
Created on Sun Feb  9 21:16:21 2020

@author: Mert Ketenci
"""
import argparse
from tqdm import tqdm
import sys
import requests
from  gensim.scripts import segment_wiki
import gzip
import re
import string
import time
import pandas as pd
import csv
sys.path.insert(0, "D:/Latent Meaning Cells/")

class get_categories:
    
    def __init__(self,URL):
        self.S = requests.Session()
        self.URL = URL
    
    def extract_categories(self,title):
        categories = []
        PARAMS = {
        "action": "query",
        "format": "json",
        "prop": "categories",
        "clshow" :"!hidden", #To show only relevent categories
        "titles": title
        }
        
        R = self.S.get(url= self.URL, params=PARAMS)
        DATA = R.json()
        PAGES = DATA["query"]["pages"]
        
        for k, v in PAGES.items():
            for cat in v['categories']:
                categories.append(cat["title"].split("Category:")[1])
        return categories

def clean_text(text):
    text = bytes(text.encode()).decode("unicode_escape")
    return ' '.join(''.join([t for t in text.replace('\n'," ").replace('\\n'," ") if t not in remove]).split()).lower()

def capitalize(title):
     return ' '.join([string.capwords(s, ' ') if s.lower() not in ["the","of"] else s for s in title.split()])

def wiki_mimicize(wikis,categories,mimicize_path):
    wikis.to_csv( mimicize_path + 'NOTEEVENTS.csv',index=False)
    with open(mimicize_path + 'CATEGORIES.csv', "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(categories)

if __name__ == '__main__':
    parser = argparse.ArgumentParser('Wiki category generator')
    parser.add_argument('--simple_bz2_path', default= sys.path[0] + 'simplewiki-latest-pages-articles.xml.bz2')
    parser.add_argument('--bz2_path', default= sys.path[0] + 'enwiki-latest-pages-articles.xml.bz2')
    parser.add_argument('--mimicize_path', default= sys.path[0])
    parser.add_argument('--simple_json_path', default= sys.path[0] + 'simplewiki-enwiki-latest.json.gz')
    parser.add_argument('--json_path', default= sys.path[0] + 'enwiki-latest.json.gz')
    parser.add_argument('--simple_wiki_URL', default= 'https://simple.wikipedia.org/w/api.php')
    parser.add_argument('--wiki_URL', default= 'https://en.wikipedia.org/w/api.php')
    parser.add_argument('--run_gensim', default= False)
    parser.add_argument('--simple_wiki', default= True)
    args = parser.parse_args()
    
    #Read wiki database using Gensim, if already read no need to re-run it    
    if args.simple_wiki:
        URL = args.simple_wiki_URL
        JSON = args.simple_json_path
        BZ2 = args.simple_bz2_path
    else:
        URL = args.wiki_URL
        JSON = args.json_path
        BZ2 = args.bz2_path
        
    if args.run_gensim:
        print("Loading data")
        wikipedia = segment_wiki
        wikipedia.segment_and_write_all_articles(args.bz2_path,JSON)    
        
    wiki_categories = get_categories(URL) 
    with gzip.GzipFile(JSON, 'r') as fin: 
        json_bytes = fin.read()
        
    json_str = json_bytes.decode('utf-8')            
    pat = r'.*?\{(.*)}.*'
    match = re.findall(pat, json_str)
    
    remove = string.punctuation.replace(".","")+"\\"
    message = "Ambiguation occured on: {}.There are several articles named as such or name mismatch."
    wikis = []
    categories = []
    NO_CATEGORY = []
    i = 0
    for m in tqdm(match, position=0, leave=True):
        wiki = dict()
        wiki["TITLE"] = bytes(re.search(r'"title": "(.*?)", "', m).group(1).encode()).decode("unicode_escape")
        wiki["TEXT"] = m.split('"section_texts": ["')[-1].replace('"]',"")
        wiki["TEXT"] = clean_text(wiki["TEXT"])
        try:
            time.sleep(0.001)
            wiki["CATEGORY"] = wiki_categories.extract_categories(wiki["TITLE"])
        except:
            print(message.format(wiki["TITLE"]))
            wiki["CATEGORY"] = "-"
            NO_CATEGORY.append(wiki["TITLE"])
        wikis.append([wiki["TITLE"],wiki["TEXT"]])
        categories.append(wiki["CATEGORY"])
        i+=1
        if i == 100:
            break
        
    wikis = pd.DataFrame(wikis, columns = ["TITLE","TEXT"])
    wiki_mimicize(wikis,categories,args.mimicize_path)




