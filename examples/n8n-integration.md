# n8n Integration Guide

## HTTP Request Node Configuration

| Setting            | Value                                                                   |
| ------------------ | ----------------------------------------------------------------------- |
| Method             | POST                                                                    |
| URL                | `http://172.17.0.1:8085/convert` (Docker) or `http://localhost:8085/convert` |
| Body Content Type  | Form-Data                                                               |
| Body Parameters    | Name: `file`, Type: n-8 Binary                                         |

## Converting File Paths to URLs

Use a **Code** node after the HTTP Request to transform paths into web-accessible URLs:

```javascript
const serverUrl = "http://your-server-ip:8080";
const items = $input.all();

for (const item of items) {
  if (item.json.images) {
    item.json.image_urls = item.json.images.map(path => {
      const parts = path.split('/output/');
      return serverUrl + '/' + parts[1];
    });
  }
}

return items;
```

## Example Workflow

```
[Trigger] → [HTTP Request: POST /convert] → [Code: path→URL] → [Send Message / Store]
```

## Response Format

```json
{
  "success": true,
  "session_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
  "images": [
    "/path/to/docs2image/output/a1b2c3d4-.../page_001.png",
    "/path/to/docs2image/output/a1b2c3d4-.../page_002.png"
  ],
  "count": 2
}
```
