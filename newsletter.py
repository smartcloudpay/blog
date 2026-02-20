import os
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from dateutil import parser
import time
import sys

# Configuration
WP_URL = os.environ.get("WP_URL", "").split('/wp-login.php')[0]
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
RSS_FEED_URL = "https://news.google.com/rss/search?q=crypto+trading+when:1d&hl=en-US&gl=US&ceid=US:en"

def get_news():
    """Fetches news from the RSS feed."""
    print("Fetching news from Google News...")
    feed = feedparser.parse(RSS_FEED_URL)
    return feed.entries

def post_to_wordpress(title, content, source_link, published_date):
    """Posts a single article to WordPress."""
    
    # Check if credentials are set
    if not WP_URL or not WP_USER or not WP_APP_PASSWORD:
        print("Error: WordPress credentials not found in environment variables.")
        return False

    api_url = f"{WP_URL}/wp-json/wp/v2/posts"
    print(f"Connecting to: {api_url}")
    
    # Create the post content with source attribution
    final_content = f"{content}<br><br><p>Source: <a href='{source_link}' target='_blank'>{source_link}</a></p>"

    # Basic Authentication
    auth = (WP_USER, WP_APP_PASSWORD)

    post_data = {
        "title": title,
        "content": final_content,
        "status": "publish", 
        "date": published_date.strftime("%Y-%m-%dT%H:%M:%S"),
        "categories": [1] # Default category, you might want to change this
    }

    try:
        # First, check if a post with this title already exists to avoid duplicates
        print(f"Checking for existing post: {title}")
        search_response = requests.get(api_url, params={"search": title}, auth=auth, timeout=10)
        print(f"Search status code: {search_response.status_code}")
        search_response.raise_for_status()
        existing_posts = search_response.json()
        
        if existing_posts:
            # Check if title matches exactly
            for post in existing_posts:
                if post['title']['rendered'] == title:
                    print(f"Skipping: Post '{title}' already exists.")
                    return True

        # If not, create the post
        response = requests.post(api_url, json=post_data, auth=auth)
        response.raise_for_status()
        
        if response.status_code == 201:
            print(f"Successfully posted: {title}")
            return True
        else:
            print(f"Failed to post: {title}. Status code: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"Error posting to WordPress: {e}")
        return False

def main():
    entries = get_news()
    print(f"Found {len(entries)} entries.")
    
    # Limit to posts from the last 24 hours to be safe, though the RSS query handles some of it
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=1)
    
    count = 0
    for entry in entries[:10]: # Limit to 10 posts per run to avoid spamming
        try:
            published_parsed = parser.parse(entry.published)
            
            # Ensure published_parsed is timezone-aware
            if published_parsed.tzinfo is None:
                 published_parsed = published_parsed.replace(tzinfo=timezone.utc)
            
            if published_parsed > cutoff:
                title = entry.title
                link = entry.link
                # RSS summary is often HTML, sometimes partial.
                # Google News RSS summary is usually just a snippet.
                # For a full automated blog, usually you want just the snippet or to scrape the full page (which is harder and more prone to errors/issues).
                # We will use the description provided in the RSS feed.
                description = entry.description
                
                success = post_to_wordpress(title, description, link, published_parsed)
                if success:
                    count += 1
                    time.sleep(2) # Be polite to the server
        except Exception as e:
            print(f"Error processing entry: {e}")
            continue

    print(f"Finished. Posted {count} new articles.")

if __name__ == "__main__":
    main()
