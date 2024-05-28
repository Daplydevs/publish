import logging
import asyncio
import tempfile
import os
import base64
import uuid
import json
from requests_toolbelt.multipart.encoder import MultipartEncoder
from aiohttp import web, ClientSession
from threading import Thread
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Predefined API key
API_KEY = "Sample_Key"

# Create a temporary directory in the same folder as the script
tmp_dir = os.path.join(os.path.dirname(__file__), 'tmp')
os.makedirs(tmp_dir, exist_ok=True)

async def upload_image_to_wordpress(image_url, alt, description, title, username, password):
    temp_file = None
    try:
        logger.info("Uploading image...")
        response = requests.get(image_url, auth=(username, password), verify=False)
        response.raise_for_status()
        logger.info("Image downloaded successfully")

        # Create a unique temporary file to store the image
        temp_file = tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False, suffix='.jpg', prefix=str(uuid.uuid4()) + '_')
        temp_file.write(response.content)
        logger.info("Image saved to temporary file: %s", temp_file.name)
        temp_file.close()

        # Upload the image to WordPress media library
        url = 'https://sits.com/wp-json/wp/v2/media/'
        with open(temp_file.name, 'rb') as file:
            file_content = file.read()

        multipart_data = MultipartEncoder(
            fields={
                'file': (title + '.jpg', file_content, 'image/jpg'),
                'alt_text': alt,
                'caption': title,
                'description': description
            }
        )

        headers = {
            'Content-Type': multipart_data.content_type,
            'Authorization': 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
        }

        response = requests.post(url, data=multipart_data, headers=headers, verify=False)
        response.raise_for_status()
        logger.info("Image uploaded successfully")

        # Extract the attachment ID from the response
        response_data = response.json()
        attachment_id = response_data.get('id')
        return attachment_id

    except requests.RequestException as e:
        logger.error("Error uploading image: %s", e)

    finally:
        if temp_file:
            # Delete the temporary file
            os.unlink(temp_file.name)
            logger.info("Temporary file deleted")

    return None

async def create_post_in_wordpress(title, author, content, status, categories, thumbnail_url, alt, description, username, password, daprun_id):
    try:
        thumbnail_attachment_id = await upload_image_to_wordpress(thumbnail_url, alt, description, title, username, password)
        if thumbnail_attachment_id:
            post_data = r"""
            {
                "title": "%s",
                "author": "%s",
                "content": "%s",
                "status": "%s",
                "categories": %s,
                "featured_media": %d
            }
            """ % (title, author, content.replace('"', '\\"').replace('\n', '\\n'), status, json.dumps(categories), thumbnail_attachment_id)
            
            url = 'https://site.com/wp-json/wp/v2/posts'
            headers = {
                'Content-Type': 'application/json',
                'Authorization': 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
            }
            response = requests.post(url, data=post_data, headers=headers, verify=False)
            response.raise_for_status()
            logger.info("Post created successfully")

            response_data = response.json()

            # Make an HTTP call to a URL with the same headers and a body of "post id, status successful" including cms_id
            await notify_successful_post(response_data['id'], daprun_id)

            return response_data

        else:
            logger.error("Failed to upload image, cannot create post")

    except requests.RequestException as e:
        logger.error("Error creating post: %s", e)

    return None

async def notify_successful_post(post_id, daprun_id):
    try:
        url = 'https://app.daply.co/api/1.1/wf/finish/'  # Replace with your actual URL
        data = {
            "post_id": post_id,
            "status": "successful",
            "daprun_id": daprun_id  # Include cms_id in the notification data
        }
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer e498c614d60653c4dca590e36af58bb4'  # Replace with your actual token
        }
        async with ClientSession() as session:
            async with session.post(url, json=data, headers=headers) as response:
                response_text = await response.text()
                if response.status == 200:
                    logging.info(f"Successfully notified: {url}")
                    return True
                else:
                    logging.warning(f"Failed to notify: {url}. Status code: {response.status}")
                    logging.warning(f"Response text: {response_text}")
                    return False
    except Exception as e:
        logging.error(f"Error in notify_successful_post: {e}")
        return False

def process_media_and_post(req_data):
    asyncio.run(process_media_and_post_async(req_data))

async def process_media_and_post_async(req_data):
    title = req_data.get('title')
    author = req_data.get('author')
    content = req_data.get('content')
    status = req_data.get('status')
    categories = req_data.get('categories')
    thumbnail_url = req_data.get('thumbnail_url')
    alt = req_data.get('alt_text')
    description = req_data.get('description')
    username = req_data.get('username')
    password = req_data.get('password')
    daprun_id = req_data.get('daprun_id')  # Get cms_id from request data

    # Handle categories as a list of integers
    if isinstance(categories, str):
        try:
            categories = json.loads(categories)
        except json.JSONDecodeError as e:
            logger.error("Error processing categories: %s", e)
            return
    elif not isinstance(categories, list):
        logger.error("Categories should be a list or a JSON-encoded string representing a list")
        return

    if title and author and content and status and categories and thumbnail_url and alt and description and username and password and daprun_id:
        logger.info("All required parameters are present")
        await create_post_in_wordpress(title, author, content, status, categories, thumbnail_url, alt, description, username, password, daprun_id)
    else:
        logger.error("Missing required parameters")
    

async def create_post(request):
    try:
        req_data = await request.json()
        logger.info("Received request data: %s", req_data)  # Log the request data
        key = request.headers.get('key')  # Retrieve 'key' from request headers

        # Validate JSON format
        try:
            json.dumps(req_data)
        except (TypeError, ValueError) as e:
            logger.error("Invalid JSON format: %s", e)
            return web.json_response({"message": f"Invalid JSON format: {e}"}, status=400)

        # Send immediate response for API key authentication
        if key == API_KEY:
            logger.info("Key authentication successful")
            # Process media and post in a separate thread
            Thread(target=process_media_and_post, args=(req_data,)).start()
            return web.json_response({"message": "Key authentication successful"}, status=200)
        else:
            logger.error("Key authentication failed")
            return web.json_response({"message": "Key authentication failed"}, status=401)

    except json.JSONDecodeError as e:
        logger.error("Error processing request JSON: %s", e)
        return web.json_response({"message": "Invalid JSON format"}, status=400)
    except Exception as e:
        logger.error("Error processing request: %s", e)
        return web.json_response({"message": f"Internal server error: {e}"}, status=500)


async def health(request):
    return web.Response(text='OK', status=200)

app = web.Application()
app.router.add_post('/api/create_post', create_post)
app.router.add_get('/', health)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))


