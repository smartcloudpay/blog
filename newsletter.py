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
WP_URL = os.environ.get("WP_URL", "").strip().rstrip('/')
if "/wp-admin" in WP_URL:
    WP_URL = WP_URL.split("/wp-admin")[0]
if "/wp-login" in WP_URL:
    WP_URL = WP_URL.split("/wp-login")[0]
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# RSS Feeds and Category IDs
CRYPTO_FEED = "https://news.google.com/rss/search?q=crypto+trading+when:1d&hl=en-US&gl=US&ceid=US:en"
TRENDING_FEED = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"

CRYPTO_CAT_ID = 2
TRENDING_CAT_ID = 3

# Model Selection
# We'll use 1.5 Flash for best free-tier stability (bypassing 2.0 flash limits)
DEFAULT_CONTENT_MODEL = 'gemini-1.5-flash-latest' 
IMAGE_MODEL = 'imagen-3.0-generate-001' 

# Generic Fallback Images (Copyright-Free / Unsplash)
CRYPTO_FALLBACK = "https://images.unsplash.com/photo-1621761191319-c6fb62002895?auto=format&fit=crop&q=80&w=800"
TRENDING_FALLBACK = "https://images.unsplash.com/photo-1504711434969-e33886168f5c?auto=format&fit=crop&q=80&w=800"

# Initialize Gemini Client
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        # Verify available models in logs to debug 404/429
        try:
            models = client.models.list()
            model_names = [m.name for m in models]
            print(f"Verified available models: {model_names}")
        except Exception as list_err:
            print(f"Warning: Could not list models: {list_err}")
    except Exception as e:
        print(f"Error initializing Gemini client: {e}")

def get_news(feed_url):
    """Fetches news from the RSS feed."""
    print(f"Fetching news from {feed_url}...")
    feed = feedparser.parse(feed_url)
    return feed.entries

def rewrite_article_and_prompt(title, summary, source_url):
    """Uses Gemini API to rewrite the article and generate an image prompt in one call."""
    if not client:
        print("Warning: Gemini client not initialized. Skipping generation.")
        return summary, title

    print(f"Generating content and image prompt for: {title}")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Combined prompt to save API calls
            prompt = f"""
            You are a professional journalist and creative director. 
            
            Task 1 (Article): Expand this news snippet into a professional blog post (300-400 words).
            Task 2 (Image Prompt): Create a short, vivid, professional photographic or high-quality digital art prompt for an image representing this news. Do not include text in the image.
            
            Original Title: {title}
            Original News Content: {summary}
            Source URL: {source_url}
            
            Writing Guidelines for Article:
            1. **Expansion**: Elaborate on implications and add analysis.
            2. **Structure**: Intro, <h2> subheadings, "Significance" section, Conclusion.
            3. **Tone**: Objective, authoritative.
            4. **Format**: Use HTML tags ONLY (<h2>, <p>, <ul>, <li>). Do not use Markdown (**bold**). Use <strong> instead.
            5. **Source**: At the end: <p><strong>Source:</strong> <a href="{source_url}">{source_url}</a></p>
            
            IMPORTANT: Output your response in this EXACT format:
            ---CONTENT---
            [HTML Content Here]
            ---PROMPT---
            [Image Prompt Here]
            """
            
            response = client.models.generate_content(
                model=DEFAULT_CONTENT_MODEL,
                contents=prompt
            )
            
            if response and response.text:
                text = response.text.strip()
                if "---CONTENT---" in text and "---PROMPT---" in text:
                    content = text.split("---CONTENT---")[1].split("---PROMPT---")[0].strip()
                    image_prompt = text.split("---PROMPT---")[1].strip()
                    
                    # Clean up potential markdown code blocks
                    content = content.replace("```html", "").replace("```", "").strip()
                    
                    print(f"✅ Successfully generated content and image prompt.")
                    return content, image_prompt
            
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = (attempt + 1) * 30 # Back off more aggressively
                print(f"Rate limited (429). Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
                continue
            print(f"Error during generation: {e}")
            break
            
    print("Warning: Generation failed. Falling back.")
    return summary, title

def generate_image(image_prompt):
    """Generates an image using Gemini Imagen 3 with fallbacks based on verified availability."""
    if not client:
        print("Warning: Gemini client not initialized. Skipping image generation.")
        return None

    # Priority 1: The experimental model seen in the user's list
    # Priority 2: Other image-capable models seen in the list
    # Priority 3: Standard Imagen names
    models_to_try = [
        IMAGE_MODEL,
        'gemini-2.5-flash-image',
        'gemini-3-pro-image-preview',
        'imagen-3.0-generate-001',
        'imagen-3.0-generate-002'
    ]

    print(f"Generating image with prompt: {image_prompt[:70]}...")
    
    for model_name in models_to_try:
        # Some SDK versions want 'imagen-3.0-generate-001', some want 'models/imagen-3.0-generate-001'
        # We'll try without the prefix first as it's more common in the SDK examples
        clean_model_name = model_name.replace('models/', '')
        
        print(f"Attempting image generation with model: {clean_model_name}")
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = client.models.generate_images(
                    model=clean_model_name,
                    prompt=image_prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        include_rai_reason=True,
                    )
                )
                
                if response and response.generated_images:
                    print(f"✅ Successfully generated image using {clean_model_name}.")
                    return response.generated_images[0].image.image_bytes
                else:
                    print(f"Warning: No images generated with {clean_model_name}.")
                    break # Try next model
                    
            except Exception as e:
                # If 404, try with the 'models/' prefix as a last resort for this specific model
                if "404" in str(e) and not clean_model_name.startswith('models/'):
                    prefixed_name = f"models/{clean_model_name}"
                    print(f"404 for {clean_model_name}. Retrying with prefix {prefixed_name}...")
                    try:
                        response = client.models.generate_images(
                            model=prefixed_name,
                            prompt=image_prompt,
                            config=types.GenerateImagesConfig(
                                number_of_images=1,
                                include_rai_reason=True,
                            )
                        )
                        if response and response.generated_images:
                            return response.generated_images[0].image.image_bytes
                    except:
                        pass
                    
                if "429" in str(e) and attempt < max_retries - 1:
                    print(f"Rate limited (429) for {clean_model_name}. Retrying in 20s...")
                    time.sleep(20)
                    continue
                print(f"Attempt failed for {clean_model_name}: {e}")
                break # Try next model
            
    print("❌ All image models failed.")
    return None

def upload_media_to_wordpress(image_bytes, filename):
    """Uploads an image to WordPress Media Library."""
    if not WP_URL or not WP_USER or not WP_APP_PASSWORD:
        print("Error: Missing WordPress credentials for media upload.")
        return None

    api_url = f"{WP_URL}/wp-json/wp/v2/media"
    auth = (WP_USER, WP_APP_PASSWORD)
    
    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "Content-Type": "image/png"
    }

    print(f"Uploading image to WordPress: {filename}")
    try:
        response = requests.post(api_url, data=image_bytes, headers=headers, auth=auth, timeout=30)
        if response.status_code == 201:
            media_id = response.json().get("id")
            print(f"✅ Successfully uploaded media. ID: {media_id}")
            return media_id
        else:
            print(f"❌ Failed to upload media. Status: {response.status_code}, Response: {response.text}")
    except Exception as e:
        print(f"❌ Exception during media upload: {e}")
    
    return None

def post_to_wordpress(title, content, source_link, published_date, featured_media_id=None, category_id=1):
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
        "categories": [category_id]
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
    for feed_url in feeds:
        entries = get_news(feed_url)
        # Choose category based on feed
        cat_id = CRYPTO_CAT_ID if feed_url == CRYPTO_FEED else TRENDING_CAT_ID
        
        feed_posted = 0
        for entry in entries:
            if posted_count >= 10 or feed_posted >= 5:
                break
                
            try:
                published_parsed = parser.parse(entry.published)
                if published_parsed.tzinfo is None:
                     published_parsed = published_parsed.replace(tzinfo=timezone.utc)
                
                if published_parsed > cutoff:
                    title = entry.title
                    link = entry.link
                    summary = getattr(entry, 'summary', getattr(entry, 'description', ""))
                    
                    print(f"\n--- {cat_id} Processing: {title} ---")
                    
                    # 1. Rewrite Content & Generate Image Prompt (Combined to save API quota)
                    rewritten_content, image_prompt = rewrite_article_and_prompt(title, summary, link)
                    
                    # Wait a bit between Gemini calls to avoid hitting RPM limits
                    time.sleep(10)
                    
                    # 2. Generate Image
                    image_bytes = generate_image(image_prompt)
                    
                    # 3. Fallback to generic image if AI fails to ensure the site looks good
                    if not image_bytes:
                        print(f"Warning: No AI image for {entry.title}. Using generic fallback.")
                        fallback_url = CRYPTO_FALLBACK if "crypto" in feed_url else TRENDING_FALLBACK
                        try:
                            response = requests.get(fallback_url, timeout=10)
                            if response.status_code == 200:
                                image_bytes = response.content
                        except Exception as img_err:
                            print(f"Error fetching fallback image: {img_err}")
                            
                    # 4. Upload to WordPress
                    media_id = None
                    if image_bytes:
                        filename = f"image_{int(time.time())}.png"
                        media_id = upload_media_to_wordpress(image_bytes, filename)
                    
                    # 5. Post to WordPress
                    success = post_to_wordpress(title, rewritten_content, link, published_parsed, media_id, cat_id)
                    if success:
                        posted_count += 1
                        feed_posted += 1
                        # Important: Sleep longer between articles to stay under free tier RPM
                        print("Waiting 30s before next article...")
                        time.sleep(30) 
            except Exception as e:
                print(f"Error processing entry: {e}")
                continue

    print(f"Finished. Posted {posted_count} new articles.")

if __name__ == "__main__":
    main()
