import os
import requests
import time
from datetime import datetime
from google import genai
from google.genai import types

# Configuration
WP_URL = os.environ.get("WP_URL", "").strip().rstrip('/')
if "/wp-admin" in WP_URL:
    WP_URL = WP_URL.split("/wp-admin")[0]
if "/wp-login" in WP_URL:
    WP_URL = WP_URL.split("/wp-login")[0]
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Category ID for Pillar Articles (can be the same as Crypto or a new one, using 2 for now)
PILLAR_CAT_ID = 2

# Model Selection
DEFAULT_CONTENT_MODEL = 'gemini-2.0-flash' 
IMAGE_MODEL = 'gemini-2.0-flash-exp-image-generation'

# Generic Fallback Image
FALLBACK_IMAGE = "https://images.unsplash.com/photo-1518546305927-5a555bb7020d?auto=format&fit=crop&q=80&w=800"

# Initialize Gemini Client
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"Error initializing Gemini client: {e}")

# Base keyword strategy for pillar articles
TARGET_KEYWORD = "keyboard crypto payment gateway"

def generate_pillar_article_and_prompt():
    """Uses Gemini API to generate a long-tail keyword, write a pillar article, and generate an image prompt."""
    if not client:
        print("Warning: Gemini client not initialized. Cannot generate article.")
        return None, None, None

    print(f"Generating pillar article based on seed keyword: {TARGET_KEYWORD}")
    
    prompt = f"""
    You are an expert SEO content strategist and technical writer.
    
    Your goal is to write a comprehensive, authoritative "pillar" blog post (800-1200 words) to help a website rank for variations of the keyword "{TARGET_KEYWORD}".
    
    Task 1: Choose a highly specific, engaging long-tail title related to "{TARGET_KEYWORD}". 
    Examples: "How Chat-Based Crypto Payments Work in 2024", "The Ultimate Guide to Integrating a Keyboard Crypto Payment Gateway", "Zero Chargebacks: The Benefits of Mobile Crypto Gateways".
    
    Task 2: Write the full article.
    - Structure it beautifully with an Introduction, <h2> and <h3> subheadings, bullet points, and a Conclusion.
    - Ensure it is informative, objective, and authoritative.
    - Naturally weave in the exact phrase "{TARGET_KEYWORD}" at least 2-3 times.
    - Format using HTML tags ONLY (<h2>, <h3>, <p>, <ul>, <li>, <strong>). No markdown (like **bold** or # headings).
    
    Task 3: Create a short, vivid, professional photographic or high-quality digital art prompt for an image representing this article. Do not include text in the image.
    
    IMPORTANT: Output your response in this EXACT format:
    ---TITLE---
    [Your Generated Title Here]
    ---CONTENT---
    [HTML Content Here]
    ---PROMPT---
    [Image Prompt Here]
    """
    
    try:
        response = client.models.generate_content(
            model=DEFAULT_CONTENT_MODEL,
            contents=prompt
        )
        
        if response and response.text:
            text = response.text.strip()
            if "---TITLE---" in text and "---CONTENT---" in text and "---PROMPT---" in text:
                title = text.split("---TITLE---")[1].split("---CONTENT---")[0].strip()
                content = text.split("---CONTENT---")[1].split("---PROMPT---")[0].strip()
                image_prompt = text.split("---PROMPT---")[1].strip()
                
                # Clean up potential markdown code blocks
                content = content.replace("```html", "").replace("```", "").strip()
                
                # Inject SEO CTA
                cta_html = '\n\n<hr>\n<p><em>Looking to integrate crypto payments directly into your mobile apps or chats? Check out <strong><a href="https://smartcloudpay.com">SmartCloudpay\'s</a></strong> keyboard crypto payment gateway API for zero chargebacks and instant settlement.</em></p>'
                content += cta_html
                
                print(f"✅ Successfully generated pillar article: {title}")
                return title, content, image_prompt
                
    except Exception as e:
        print(f"Error during generation: {e}")
        
    return None, None, None

def generate_image(image_prompt):
    """Generates an image using Gemini Imagen 3 with fallbacks based on verified availability."""
    if not client:
        return None

    models_to_try = [
        IMAGE_MODEL,
        'imagen-3.0-generate-001',
        'imagen-3.0-generate-002',
        'gemini-2.5-flash-image'
    ]

    print(f"Generating image with prompt: {image_prompt[:70]}...")
    
    for model_name in models_to_try:
        clean_model_name = model_name.replace('models/', '')
        print(f"Attempting image generation with model: {clean_model_name}")
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
                
        except Exception as e:
            if "404" in str(e) and not clean_model_name.startswith('models/'):
                prefixed_name = f"models/{clean_model_name}"
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
            print(f"Attempt failed for {clean_model_name}: {e}")
            
    print("❌ All AI image models failed.")
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
        response = requests.post(api_url, data=image_bytes, headers=headers, auth=auth, timeout=30)
        if response.status_code == 201:
            media_id = response.json().get("id")
            print(f"✅ Successfully uploaded media. ID: {media_id}")
            return media_id
    except Exception as e:
        print(f"❌ Exception during media upload: {e}")
    
    return None

def post_to_wordpress(title, content, featured_media_id=None, category_id=1):
    """Posts a single article to WordPress."""
    if not WP_URL or not WP_USER or not WP_APP_PASSWORD:
        print("Error: WordPress credentials not found.")
        return False

    api_url = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USER, WP_APP_PASSWORD)

    post_data = {
        "title": title,
        "content": content,
        "status": "publish", 
        "categories": [category_id]
    }

    if featured_media_id:
        post_data["featured_media"] = featured_media_id

    try:
        response = requests.post(api_url, json=post_data, auth=auth)
        response.raise_for_status()
        
        if response.status_code == 201:
            print(f"✅ Successfully posted: {title}")
            return True
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Error posting to WordPress: {e}")
        return False

def main():
    print("Starting Automated Pillar Article Generator...")
    
    title, content, image_prompt = generate_pillar_article_and_prompt()
    
    if not title or not content:
        print("Failed to generate article. Exiting.")
        return
        
    image_bytes = generate_image(image_prompt)
    
    if not image_bytes:
        print(f"Warning: No AI image generated. Using generic fallback.")
        try:
            response = requests.get(FALLBACK_IMAGE, timeout=15)
            if response.status_code == 200:
                image_bytes = response.content
        except Exception as img_err:
            print(f"Error fetching fallback image: {img_err}")
            
    media_id = None
    if image_bytes:
        filename = f"pillar_{int(time.time())}.png"
        media_id = upload_media_to_wordpress(image_bytes, filename)
        
    success = post_to_wordpress(title, content, media_id, PILLAR_CAT_ID)
    if success:
         print("🎉 Pillar article workflow completed successfully!")

if __name__ == "__main__":
    main()
