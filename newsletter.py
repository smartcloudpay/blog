import os
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from dateutil import parser
import time
import sys
from google import genai
from google.genai import types
import base64
from io import BytesIO

# Configuration
WP_URL = os.environ.get("WP_URL", "").split('/wp-login.php')[0]
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyBvwwxY7Hv8HQccKmSKynNyzskKV__dHx4")

# RSS Feeds
CRYPTO_FEED = "https://news.google.com/rss/search?q=crypto+trading+when:1d&hl=en-US&gl=US&ceid=US:en"
TRENDING_FEED = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"

# Initialize Gemini Client
client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)

def get_news(feed_url):
    """Fetches news from the RSS feed."""
    print(f"Fetching news from {feed_url}...")
    feed = feedparser.parse(feed_url)
    return feed.entries

def rewrite_article(title, summary, source_url):
    """Uses Gemini API to rewrite and expand the article snippet."""
    if not client:
        print("Warning: Gemini client not initialized. Skipping rewrite.")
        return summary

    print(f"Rewriting article: {title}")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Enhanced prompt for better expansion
            prompt = f"""
            You are a professional journalist for a major news outlet. 
            Your task is to take a short news snippet and expand it into a full-length, professional blog post (300-400 words).
            
            Original Title: {title}
            Original News Content: {summary}
            Source URL: {source_url}
            
            Writing Guidelines:
            1. **Expansion**: Do not just rewrite the snippet. Research or elaborate on the implications of this news and add expert-level analysis.
            2. **Structure**: 
               - Start with an engaging introduction.
               - Use <h2> subheadings to break down the key points.
               - Include a "Market Impact" (if financial) or "Significance" section.
               - End with a conclusion or future outlook.
            3. **Tone**: Objective, professional, and authoritative.
            4. **Format**: Use HTML tags ONLY (<h2>, <p>, <ul>, <li>). Do not use Markdown (**bold**). Use <strong> instead.
            5. **Source**: At the very end, add: <p><strong>Source:</strong> <a href="{source_url}">{source_url}</a></p>
            
            IMPORTANT: Your output will be posted directly to WordPress. Do not include any title or preamble. Just the HTML body.
            """
            
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt
            )
            
            if response and response.text:
                content = response.text.strip()
                # Clean up potential markdown code blocks if Gemini includes them
                if content.startswith("```html"):
                    content = content.replace("```html", "").replace("```", "").strip()
                elif content.startswith("```"):
                    content = content.replace("```", "").strip()
                
                print(f"Successfully generated rewritten content ({len(content)} characters)")
                return content
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                print(f"Rate limited (429). Retrying in 15s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(15)
                continue
            print(f"Error during rewriting: {e}")
            break
            
    print("Warning: Rewriting failed. Falling back to snippet.")
    return summary

def generate_image(title, content):
    """Generates an image using Gemini Imagen 3."""
    if not client:
        print("Warning: Gemini client not initialized. Skipping image generation.")
        return None

    print(f"Generating image for: {title}")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Generate a descriptive prompt for Imagen
            prompt_request = f"Create a short, vivid, professional photographic or high-quality digital art prompt for an image representing this news: '{title}'. The image should be suitable for a news blog featured image. Do not include text in the image."
            prompt_response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt_request
            )
            
            if not prompt_response or not prompt_response.text:
                image_prompt = title
            else:
                image_prompt = prompt_response.text.strip()
                
            print(f"Image Prompt: {image_prompt}")

            # Use the new SDK for Imagen 3
            response = client.models.generate_content(
                model='imagen-3.0-generate-001',
                contents=image_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type='image/png'
                )
            )
            
            if response.generated_images:
                return response.generated_images[0].image.image_bytes
                
            return None
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                print(f"Rate limited (429). Retrying in 20s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(20)
                continue
            print(f"Error during image generation: {e}")
            break
            
    return None

def upload_media_to_wordpress(image_bytes, filename):
    """Uploads an image to WordPress Media Library."""
    if not WP_URL or not WP_USER or not WP_APP_PASSWORD:
        return None

    api_url = f"{WP_URL}/wp-json/wp/v2/media"
    auth = (WP_USER, WP_APP_PASSWORD)
    
    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "Content-Type": "image/png"
    }

    try:
        response = requests.post(api_url, data=image_bytes, headers=headers, auth=auth)
        response.raise_for_status()
        if response.status_code == 201:
            media_id = response.json().get("id")
            print(f"Successfully uploaded media. ID: {media_id}")
            return media_id
    except Exception as e:
        print(f"Error uploading media to WordPress: {e}")
    
    return None

def post_to_wordpress(title, content, source_link, published_date, featured_media_id=None):
    """Posts a single article to WordPress."""
    
    # Check if credentials are set
    if not WP_URL or not WP_USER or not WP_APP_PASSWORD:
        print("Error: WordPress credentials not found in environment variables.")
        return False

    api_url = f"{WP_URL}/wp-json/wp/v2/posts"
    print(f"Connecting to: {api_url}")
    
    # Basic Authentication
    auth = (WP_USER, WP_APP_PASSWORD)

    post_data = {
        "title": title,
        "content": content,
        "status": "publish", 
        "date": published_date.strftime("%Y-%m-%dT%H:%M:%S"),
        "categories": [1] # Default category
    }

    if featured_media_id:
        post_data["featured_media"] = featured_media_id

    try:
        # First, check if a post with this title already exists to avoid duplicates
        print(f"Checking for existing post: {title}")
        search_response = requests.get(api_url, params={"search": title}, auth=auth, timeout=10)
        search_response.raise_for_status()
        existing_posts = search_response.json()
        
        if existing_posts:
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
    # Process both feeds
    feeds = [CRYPTO_FEED, TRENDING_FEED]
    
    all_entries = []
    for feed_url in feeds:
        entries = get_news(feed_url)
        all_entries.extend(entries)
    
    print(f"Found total {len(all_entries)} entries.")
    
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=1)
    
    posted_count = 0
    # Process up to 5 posts per feed to avoid hitting limits or being spammy
    for entry in all_entries:
        if posted_count >= 10:
            break
            
        try:
            published_parsed = parser.parse(entry.published)
            if published_parsed.tzinfo is None:
                 published_parsed = published_parsed.replace(tzinfo=timezone.utc)
            
            if published_parsed > cutoff:
                title = entry.title
                link = entry.link
                summary = getattr(entry, 'summary', getattr(entry, 'description', ""))
                
                print(f"\n--- Processing: {title} ---")
                
                # 1. Rewrite Content
                rewritten_content = rewrite_article(title, summary, link)
                
                # 2. Generate Image
                image_bytes = generate_image(title, rewritten_content)
                
                media_id = None
                if image_bytes:
                    # 3. Upload to WordPress
                    filename = f"image_{int(time.time())}.png"
                    media_id = upload_media_to_wordpress(image_bytes, filename)
                
                # 4. Post to WordPress
                success = post_to_wordpress(title, rewritten_content, link, published_parsed, media_id)
                if success:
                    posted_count += 1
                    time.sleep(5) # Delay to be polite and allow processing
        except Exception as e:
            print(f"Error processing entry: {e}")
            continue

    print(f"Finished. Posted {posted_count} new articles.")

if __name__ == "__main__":
    main()
