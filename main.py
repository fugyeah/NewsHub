import os
import configparser
from datetime import datetime
from Flask import Flask, render_template
import time
import json
from sentence_transformers import SentenceTransformer
import pyfiglet

# Create an ASCII header
header = pyfiglet.figlet_format("NewsPlanetAi")

print(header)
print("Loading Configs")
# Load the configuration file
config = configparser.ConfigParser()
config.read('modules/suite_config.ini')

# Access variables
openai_api_key = config['OPENAI']['OPENAI_API_KEY']
ftp_host = config['FTP']['Host']
ftp_user = config['FTP']['User']
ftp_password = config['FTP']['Password']
ftp_directory = config['FTP']['Directory']

cache_directory = config['Cache']['Directory']
max_cache_age_hours = int(config['Cache']['MaxAgeHours'])
cache_file = config['Cache']['DailyCacheFile']
weekly_cache_file = config['Cache']['WeeklyCacheFile']


max_summary_length = int(config['Summaries']['MaxSummaryLength'])
max_cache_age_hours = int(config['Summaries']['MaxCacheAgeHours'])

categories = config['Headlines']['Categories'].split(', ')

use_tqdm = config.getboolean('General', 'UseTqdm')

model_categorize_headlines = config['Models']['CategorizeHeadlines']
model_summarize_super_summary = config['Models']['SummarizeSuperSummary']
SimilarityModel = config['Models']['SimilarityModel']

# Thresholds
SIMILARITY_THRESHOLD = config.getfloat('THRESHOLDS', 'SIMILARITY_THRESHOLD')
TOP_N_ARTICLES = config.getint('THRESHOLDS', 'TOP_N_ARTICLES')

retries_summarize_articles = config.getint('Retry', 'SummarizeArticlesRetries')
wait_time_seconds_summarize_articles = config.getint('Retry', 'SummarizeArticlesWaitTimeSeconds')
retries_summarize_super_summary = config.getint('Retry', 'SummarizeSuperSummaryRetries')
wait_time_seconds_summarize_super_summary = config.getint('Retry', 'SummarizeSuperSummaryWaitTimeSeconds')
# List of news sources
sources = [
    ("https://www.baynews9.com/services/contentfeed.fl%7Ctampa%7Cpolitics.hero.rss", 3),
    ("https://feeder-prod.int.politico.com/feeds/rss/politicoflorida.xml", 2),
    ("https://floridapolitics.com/feed/", 3),
    ("https://www.politicalcortadito.com/feed/", 2),
    ("https://www.blogger.com/feeds/6932198920118082868/posts/default", 3),
    ("https://thefloridasqueeze.com/feed/", 2),
    ("https://www.floridapoliticalreview.com/feed/", 2),
    ("https://thefloridapundit.com/category/politics/feed/", 2),
    ("https://www.nbcmiami.com/news/politics/feed/", 1),
    ("https://www.wfla.com/news/politics/feed/", 3),
    ("https://feeds.mcclatchy.com/miamiherald/sections/news/politics-government/state-politics/stories", 3),
    ("https://www.flsenate.gov/Tracker/RSS/DailyCalendar", 3),
    ("https://www.flsenate.gov/Tracker/RSS/Video", 3),
    ("https://www.flsenate.gov/Tracker/RSS/PressReleases",3),
    ("https://www.floridadisaster.org/rss-morning-sitrep/?basic=true", 3),
    ("https://www.floridadisaster.org/rss-NHC-tropical-weather-discussion/", 5),
    ("https://www.govinfo.gov/rss/uscourts-flsd.xml", 3),
    ("https://www.govinfo.gov/rss/uscourts-flnd.xml", 5),
    ("https://floridasadventurecoast.com/feed/",5),
    ("https://thefloridachannel.org/videos/feed/", 3),
    ("https://www.omnycontent.com/d/playlist/a858b0a5-e5e6-4a14-9717-a70b010facc1/70fa6451-35ad-4b66-ba76-a93e00e6cbae/ce2e8a26-165b-4565-b1d8-a93e00e7df2a/podcast.rss", 5),
    ("https://rotundapodcast.podomatic.com/rss2.xml", 5),
    ("https://anchor.fm/s/5ce9855c/podcast/rss", 5),
    ("https://anchor.fm/s/5024ce58/podcast/rss", 5),
    ("https://feeds.fireside.fm/sunrise/rss", 3),
]

from modules import scraper
from modules import classifier
from modules import summarizer
from modules import cache_files
from modules import ftp_uploader
from modules import sum_summaries
from modules import locations
from modules import similarity


app = Flask(__name__)
def format_datetime(value, fmt='%Y-%m-%d %H:%M:%S'):
    if value is None:  # Add this line
        return ''
    elif isinstance(value, time.struct_time):
        value = datetime.fromtimestamp(time.mktime(value))
    return value.strftime(fmt)

app.jinja_env.filters['strftime'] = format_datetime

def main():
    print("Running NewsPlanetAi System...")
    if not os.path.exists(cache_directory):
        os.makedirs(cache_directory)

    if cache_files.is_cache_valid(cache_file, max_cache_age=max_cache_age_hours):
        try:
            summaries = cache_files.load_cache(cache_file)
        except Exception as e:
            print(f"Error while loading cache: {e}")
            summaries = []
    else:
        # Initialize an empty list for all_headlines
        all_headlines = []

        # Iterate over the sources
        for i, (source, num_articles) in enumerate(sources):
            print(f"Scraping headlines from source {i + 1}/{len(sources)}: {source}")
            headlines = scraper.scrape_headlines(source, num_articles)
            all_headlines.extend(headlines)
            print(f"Finished scraping headlines from source {i + 1}/{len(sources)}: {source}\n")

        categorized_headlines = classifier.categorize_headlines(all_headlines)
        summaries = summarizer.summarize_articles(categorized_headlines, retries_summarize_articles, wait_time_seconds_summarize_articles)
        cache_files.save_cache(cache_file, summaries)

    # Generate top articles by category
    print("Grouping summaries by category")
    summaries_by_categories = similarity.group_by_category(summaries)
    print("Loading model")
    model = SentenceTransformer(SimilarityModel)
    print("Generating top articles by category")
    top_articles_by_category = similarity.generate_top_articles_by_category(summaries_by_categories, model, SIMILARITY_THRESHOLD, TOP_N_ARTICLES)

    # Extract locations and get coordinates
    extracted_locations = locations.extract_locations(summaries)
    coordinates = locations.get_coordinates(extracted_locations) # Get coordinates
    geojson_data, geojson_file_name = locations.generate_geojson(summaries, extracted_locations, coordinates)


    # Attach locations and coordinates to each summary
    for i in range(len(summaries)):
        summaries[i] = summaries[i] + (extracted_locations[i], coordinates[i])

    cache_files.save_to_weekly_cache(summaries, weekly_cache_file)
    organized_summaries = summarizer.organize_summaries_by_category(summaries)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Get the summarized summaries from the daily cache
    summarized_summaries = summarizer.summarize_daily_cache(cache_file)

    # Generate the news broadcast script
    super_summary_text = sum_summaries.compile_super_summary(summarized_summaries)

    # Generate and save JSON
    news = {
        "timestamp": timestamp,
        "super_summary": super_summary_text,
        "categories": []
    }

    for category, summaries in organized_summaries.items():
        news_category = {
            "name": category,
            "summaries": []
        }
        for summary in summaries:
            news_summary = {
                "headline": summary[0],
                "summary": summary[1],
                "link": summary[2],
                "timestamp": format_datetime(summary[3]),
                "source": summary[4],
                "location": summary[5] if summary[5] is not None else "None",
                "coordinates": list(summary[6]) if summary[6] is not None else [None, None],
                "top_headline": False  # Default value
            }

            # Check if the summary is a top article
            if category in top_articles_by_category:
                for top_article in top_articles_by_category[category]:
                    if summary[0] == top_article[0][0]:  # Compare headlines
                        news_summary["top_headline"] = True
                        break

            news_category["summaries"].append(news_summary)
        news["categories"].append(news_category)


    # Save the updated news data to a JSON file
    with open('news.json', 'w', encoding='utf-8') as f:
        json.dump(news, f, ensure_ascii=False, indent=4)

    # Upload the JSON file to the server
    ftp_uploader.upload_to_ftp('news.json', ftp_host, ftp_user, ftp_password, ftp_directory, 'news.json')
    ftp_uploader.upload_to_ftp(geojson_file_name, ftp_host, ftp_user, ftp_password, ftp_directory, 'modular_geojson.json')


    # Return the generated news data
    return news



if __name__ == "__main__":
    main()
