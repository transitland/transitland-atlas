#!/usr/bin/env python3
"""
Review French operators and feeds from Transitland.
Searches for French operators via REST API, checks feed age, and matches with transport.data.gouv.fr datasets.
"""
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "requests",
#     "thefuzz",
# ]
# ///

import os
import sys
import json
import argparse
import re
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlencode

import requests
from thefuzz import fuzz, process


class Tee:
    """Write to both stdout and a file."""
    def __init__(self, file_path):
        self.file = open(file_path, 'w', encoding='utf-8')
        self.stdout = sys.stdout
    
    def write(self, text):
        self.stdout.write(text)
        self.file.write(text)
        self.file.flush()
    
    def flush(self):
        self.stdout.flush()
        self.file.flush()
    
    def close(self):
        self.file.close()


class CacheManager:
    """Manage local caching of API responses."""
    def __init__(self, cache_dir: Path = Path('.cache'), ttl_hours: int = 24):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(exist_ok=True)
        self.ttl = timedelta(hours=ttl_hours)
    
    def _cache_key(self, url: str, params: Optional[Dict] = None) -> str:
        """Generate cache key from URL and params."""
        key_str = url
        if params:
            # Sort params for consistent keys
            sorted_params = sorted(params.items())
            key_str += '?' + urlencode(sorted_params)
        return hashlib.sha256(key_str.encode()).hexdigest()
    
    def _cache_path(self, key: str) -> Path:
        """Get cache file path for a key."""
        return self.cache_dir / f"{key}.json"
    
    def get(self, url: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Get cached response if available and not expired."""
        key = self._cache_key(url, params)
        cache_file = self._cache_path(key)
        
        if not cache_file.exists():
            return None
        
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            
            # Check if expired
            cached_time = datetime.fromisoformat(cached['cached_at'])
            if datetime.now() - cached_time > self.ttl:
                # Expired, delete cache file
                cache_file.unlink()
                return None
            
            return cached.get('data')
        except:
            # If cache is corrupted, delete it
            if cache_file.exists():
                cache_file.unlink()
            return None
    
    def set(self, url: str, data: Dict, params: Optional[Dict] = None):
        """Cache a response."""
        key = self._cache_key(url, params)
        cache_file = self._cache_path(key)
        
        cache_data = {
            'url': url,
            'params': params,
            'cached_at': datetime.now().isoformat(),
            'data': data
        }
        
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2)
    
    def clear(self):
        """Clear all cache files."""
        for cache_file in self.cache_dir.glob('*.json'):
            cache_file.unlink()


def fetch_transitland_operators(api_base: str = "https://api.transit.land/api/v2", adm0_iso: str = "FR", limit: int = 100, apikey: Optional[str] = None, cache: Optional[CacheManager] = None) -> List[Dict]:
    """Fetch French operators from Transitland REST API with caching."""
    # Try cache first
    if cache:
        cached = cache.get(f"{api_base}/rest/operators", {'adm0_iso': adm0_iso})
        if cached:
            print(f"Using cached operators data")
            return cached
    
    operators = []
    after = None
    
    print(f"Fetching French operators (adm0_iso={adm0_iso})...")
    
    headers = {}
    if apikey:
        headers['apikey'] = apikey
    
    while True:
        params = {
            'adm0_iso': adm0_iso,
            'limit': limit,
        }
        if after:
            params['after'] = after
        
        try:
            response = requests.get(f"{api_base}/rest/operators", params=params, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            page_operators = data.get('operators', [])
            if not page_operators:
                break
            
            operators.extend(page_operators)
            print(f"  Fetched {len(operators)} operators so far...")
            
            # Check for pagination
            meta = data.get('meta', {})
            after = meta.get('after')
            if not after:
                break
                
        except requests.exceptions.RequestException as e:
            print(f"Error fetching operators: {e}")
            break
    
    print(f"Found {len(operators)} French operators")
    
    # Cache the result
    if cache:
        cache.set(f"{api_base}/rest/operators", operators, {'adm0_iso': adm0_iso})
    
    return operators


def fetch_operator_feeds(api_base: str, operator_onestop_id: str, apikey: Optional[str] = None, cache: Optional[CacheManager] = None) -> List[Dict]:
    """Fetch feeds associated with an operator with caching."""
    headers = {}
    if apikey:
        headers['apikey'] = apikey
    
    # Try cache first
    if cache:
        cached = cache.get(f"{api_base}/rest/operators/{operator_onestop_id}", None)
        if cached:
            # Cache stores the full operator response, extract feeds
            operators = cached.get('operators', [])
            if operators:
                feeds = operators[0].get('feeds', [])
                # Return basic feed info from cache (we'll fetch details if needed)
                return feeds
    
    try:
        response = requests.get(
            f"{api_base}/rest/operators/{operator_onestop_id}",
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        # API returns {"operators": [...]}
        operators = data.get('operators', [])
        if not operators:
            return []
        
        operator = operators[0]
        feeds = operator.get('feeds', [])
        
        # Cache the full operator response
        if cache:
            cache.set(f"{api_base}/rest/operators/{operator_onestop_id}", data, None)
        
        # Fetch detailed feed information
        detailed_feeds = []
        for feed in feeds:
            feed_id = feed.get('onestop_id')
            if feed_id:
                # Try cache for individual feed
                if cache:
                    cached_feed = cache.get(f"{api_base}/rest/feeds/{feed_id}", None)
                    if cached_feed:
                        detailed_feeds.append(cached_feed)
                        continue
                
                try:
                    feed_response = requests.get(
                        f"{api_base}/rest/feeds/{feed_id}",
                        headers=headers,
                        timeout=30
                    )
                    feed_response.raise_for_status()
                    feed_data = feed_response.json()
                    feed_obj = feed_data.get('feed', feed)
                    detailed_feeds.append(feed_obj)
                    
                    # Cache individual feed
                    if cache:
                        cache.set(f"{api_base}/rest/feeds/{feed_id}", feed_obj, None)
                except:
                    # Fallback to basic feed info
                    detailed_feeds.append(feed)
        
        return detailed_feeds
    except requests.exceptions.RequestException as e:
        print(f"  Error fetching feeds for {operator_onestop_id}: {e}")
        return []


def get_feed_age(api_base: str, feed_id: str, apikey: Optional[str] = None, cache: Optional[CacheManager] = None) -> Optional[Tuple[int, str, Optional[str]]]:
    """Get age of feed in days, last update timestamp, and active feed version info.
    
    Returns: (age_days, fetched_at, active_feed_version_sha1) or None
    """
    headers = {}
    if apikey:
        headers['apikey'] = apikey
    
    try:
        # Try cache for feed versions
        if cache:
            cached = cache.get(f"{api_base}/rest/feeds/{feed_id}/feed_versions", {'limit': 1})
            if cached:
                feed_versions = cached.get('feed_versions', [])
                if feed_versions:
                    latest = feed_versions[0]
                    fetched_at = latest.get('fetched_at')
                    sha1 = latest.get('sha1')
                    if fetched_at:
                        try:
                            dt = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
                            now = datetime.now(timezone.utc)
                            days = (now - dt).total_seconds() / 86400
                            return (int(days), fetched_at, sha1)
                        except:
                            pass
        
        # Fetch feed versions - try to get most recent first
        response = requests.get(
            f"{api_base}/rest/feeds/{feed_id}/feed_versions",
            params={'limit': 1},
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        feed_versions = data.get('feed_versions', [])
        
        # Cache the response
        if cache:
            cache.set(f"{api_base}/rest/feeds/{feed_id}/feed_versions", data, {'limit': 1})
        
        if feed_versions:
            # Get most recent version (should be first)
            latest = feed_versions[0]
            fetched_at = latest.get('fetched_at')
            sha1 = latest.get('sha1')
            
            if fetched_at:
                try:
                    dt = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    days = (now - dt).total_seconds() / 86400
                    return (int(days), fetched_at, sha1)
                except Exception as e:
                    pass
    except requests.exceptions.RequestException as e:
        # Silently fail - feed might not have versions endpoint
        pass
    
    return None


def fetch_french_datasets_api(cache: Optional[CacheManager] = None) -> Optional[List[Dict]]:
    """Fetch French transport portal datasets from API with caching."""
    api_url = "https://transport.data.gouv.fr/api/datasets"
    
    # Try cache first
    if cache:
        cached = cache.get(api_url, None)
        if cached:
            print(f"Using cached French portal datasets from API")
            return cached
    
    try:
        print(f"Fetching French transport portal datasets from API...")
        response = requests.get(api_url, timeout=60)
        response.raise_for_status()
        datasets = response.json()
        
        # Cache the result
        if cache:
            cache.set(api_url, datasets, None)
        
        return datasets
    except requests.exceptions.RequestException as e:
        print(f"Error fetching French portal datasets from API: {e}")
        return None


def load_french_datasets(cache: Optional[CacheManager] = None) -> List[Dict]:
    """Load and parse French transport portal datasets from API (with caching)."""
    # Fetch from API (with caching)
    api_datasets = fetch_french_datasets_api(cache)
    if not api_datasets:
        print("Error: Failed to fetch French portal datasets from API")
        return []
    datasets = api_datasets
    
    # Filter to GTFS datasets only
    gtfs_datasets = []
    for dataset in datasets:
        # Check if it has GTFS resources
        has_gtfs = False
        gtfs_resources = []
        networks = []
        location_info = None
        
        for resource in dataset.get('resources', []):
            if resource.get('format', '').upper() == 'GTFS':
                has_gtfs = True
                # Extract network names from metadata if available
                metadata = resource.get('metadata', {})
                if metadata and 'networks' in metadata:
                    networks = metadata.get('networks', [])
                
                gtfs_resources.append({
                    'url': resource.get('url'),
                    'original_url': resource.get('original_url'),
                    'title': resource.get('title'),
                    'updated': resource.get('updated'),
                    'metadata': metadata
                })
        
        if has_gtfs:
            # Extract slug from page_url if not present
            slug = dataset.get('slug')
            if not slug and dataset.get('page_url'):
                # Extract slug from URL like: https://transport.data.gouv.fr/datasets/slug-here
                page_url = dataset.get('page_url', '')
                if '/datasets/' in page_url:
                    slug = page_url.split('/datasets/')[-1].split('?')[0].strip()
            
            # Extract location info from covered_area
            covered_area = dataset.get('covered_area', [])
            if covered_area:
                # Prefer city/commune, then EPCI, then region
                for area in covered_area:
                    if area.get('type') in ['commune', 'epci']:
                        location_info = {
                            'type': area.get('type'),
                            'nom': area.get('nom'),
                            'insee': area.get('insee')
                        }
                        break
                if not location_info:
                    for area in covered_area:
                        if area.get('type') == 'region':
                            location_info = {
                                'type': area.get('type'),
                                'nom': area.get('nom'),
                                'insee': area.get('insee')
                            }
                            break
            
            gtfs_datasets.append({
                'id': dataset.get('id'),
                'datagouv_id': dataset.get('datagouv_id'),
                'title': dataset.get('title'),
                'slug': slug,
                'page_url': dataset.get('page_url'),
                'licence': dataset.get('licence'),
                'networks': networks,
                'location_info': location_info,
                'publisher': dataset.get('publisher', {}).get('name'),
                'gtfs_resources': gtfs_resources
            })
    
    print(f"Loaded {len(gtfs_datasets)} GTFS datasets from French portal")
    return gtfs_datasets


def fuzzy_match_operator(operator: Dict, french_datasets: List[Dict], threshold: int = 60) -> List[Tuple[Dict, int]]:
    """Fuzzy match operator with French portal datasets."""
    operator_name = operator.get('name', '')
    operator_short_name = operator.get('short_name', '')
    
    # Build search terms
    search_terms = []
    if operator_name:
        search_terms.append(operator_name)
    if operator_short_name and operator_short_name != operator_name:
        search_terms.append(operator_short_name)
    
    if not search_terms:
        return []
    
    matches = []
    for dataset in french_datasets:
        dataset_title = dataset.get('title', '')
        dataset_slug = dataset.get('slug', '')
        
        # Try matching against title and slug
        best_score = 0
        for term in search_terms:
            if term:
                score_title = fuzz.partial_ratio(term.lower(), dataset_title.lower())
                score_slug = fuzz.partial_ratio(term.lower(), dataset_slug.lower())
                best_score = max(best_score, score_title, score_slug)
        
        if best_score >= threshold:
            matches.append((dataset, best_score))
    
    # Sort by score descending
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches


def fuzzy_match_feed(feed: Dict, french_datasets: List[Dict], threshold: int = 60) -> List[Tuple[Dict, int]]:
    """Fuzzy match feed with French portal datasets."""
    feed_id = feed.get('onestop_id', '')
    feed_url = feed.get('urls', {}).get('static_current', '')
    
    # Extract search terms from feed ID
    search_terms = []
    feed_id_parts = feed_id.replace('f-', '').split('~')
    for part in feed_id_parts:
        if part not in ['fr', 'france', 'bus', 'gtfs', 'rt', 'realtime'] and len(part) > 3:
            search_terms.append(part)
    
    # Extract from URL
    if 'tours' in feed_url.lower():
        search_terms.append('tours')
    if 'filbleu' in feed_url.lower() or 'fil-bleu' in feed_url.lower():
        search_terms.append('fil bleu')
    if 'chamonix' in feed_url.lower():
        search_terms.append('chamonix')
    if 'smtd' in feed_url.lower() or 'douaisis' in feed_url.lower() or 'douai' in feed_url.lower():
        search_terms.extend(['douai', 'smtd', 'eveole'])
    
    if not search_terms:
        return []
    
    matches = []
    for dataset in french_datasets:
        dataset_title = dataset.get('title', '').lower()
        dataset_slug = dataset.get('slug', '').lower()
        
        best_score = 0
        for term in search_terms:
            if term and len(term) > 2:
                score_title = fuzz.partial_ratio(term.lower(), dataset_title)
                score_slug = fuzz.partial_ratio(term.lower(), dataset_slug)
                best_score = max(best_score, score_title, score_slug)
        
        if best_score >= threshold:
            matches.append((dataset, best_score))
    
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches


def check_url_outdated(url: str) -> bool:
    """Check if URL looks outdated."""
    url_lower = url.lower()
    outdated_patterns = [
        'excellance.fr',
        'data.gouv.fr/api/1/datasets/r/',  # Old API format
    ]
    return any(pattern in url_lower for pattern in outdated_patterns)


def extract_domain_from_url(url: str) -> Optional[str]:
    """Extract domain from URL for DMFR file naming."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return None
        # Remove www. prefix
        if hostname.startswith('www.'):
            hostname = hostname[4:]
        return hostname
    except:
        return None


def generate_feed_id_from_dataset(dataset: Dict) -> str:
    """Generate a feed ID from dataset using networks, title, location, and slug."""
    parts = []
    
    # Priority 1: Use network name from metadata (most specific)
    networks = dataset.get('networks', [])
    if networks:
        # Use first network name, cleaned up
        network = networks[0].lower()
        # Remove common words
        network = re.sub(r'\b(reseau|network|transports?|transport|urbain|urbains?)\b', '', network)
        network = re.sub(r'[^a-z0-9]+', '', network).strip()
        if network and len(network) >= 2:
            parts.append(network)
    
    # Priority 2: Extract key words from title
    title = dataset.get('title', '').lower()
    if title:
        # Remove common French transit words
        title_clean = re.sub(
            r'\b(reseau|network|transports?|transport|urbain|urbains?|donnees|données|horaires|theoriques|théoriques|lignes|arrets|arrêts|gtfs|fichier|offre|de|du|des|la|le|les|et|en|pour)\b',
            '',
            title
        )
        # Extract meaningful words (3+ chars)
        words = [w for w in re.split(r'[^a-z0-9]+', title_clean) if len(w) >= 3]
        # Take first 2-3 meaningful words
        if words:
            key_words = words[:2] if len(words) >= 2 else words[:1]
            for word in key_words:
                if word not in parts:  # Avoid duplicates with network
                    parts.append(word)
    
    # Priority 3: Add location if available
    location_info = dataset.get('location_info')
    if location_info:
        location_name = location_info.get('nom', '').lower()
        if location_name:
            # Extract key part of location name (city name, not full description)
            location_clean = re.sub(r'\b(communaute|communauté|metropole|métropole|region|région|pays|epci|cc|ca)\b', '', location_name)
            location_clean = re.sub(r'[^a-z0-9]+', '', location_clean).strip()
            if location_clean and len(location_clean) >= 2:
                # Only add if it's a short, meaningful name
                if len(location_clean) <= 15:
                    parts.append(location_clean)
    
    # Priority 4: Fallback to slug if we don't have good parts
    if not parts:
        slug = dataset.get('slug', '')
        if slug:
            # Clean up slug
            feed_id = slug.lower()
            # Remove common prefixes
            feed_id = re.sub(r'^(fr-|reseau-|offre-de-|donnees-|horaires-|gtfs-|fichier-|arrets-)', '', feed_id)
            # Replace dashes and special chars with ~
            feed_id = re.sub(r'[^a-z0-9]+', '~', feed_id)
            # Remove leading/trailing ~
            feed_id = feed_id.strip('~')
            # Limit length
            if len(feed_id) > 40:
                feed_id = feed_id[:40]
            if feed_id:
                parts.append(feed_id)
    
    # Combine parts
    if parts:
        feed_id = '~'.join(parts[:3])  # Max 3 parts
        # Clean up: remove any remaining special chars
        feed_id = re.sub(r'[^a-z0-9~]+', '', feed_id)
        feed_id = feed_id.strip('~')
    else:
        # Ultimate fallback: use first few chars of title
        feed_id = re.sub(r'[^a-z0-9]+', '~', title[:30]).strip('~')
    
    # Ensure reasonable length
    if len(feed_id) > 50:
        feed_id = feed_id[:50].rstrip('~')
    
    # Add country code for French feeds if not present
    if feed_id and not feed_id.endswith('~fr') and 'fr' not in feed_id:
        # Only add if it's a short ID (to avoid making it too long)
        if len(feed_id) < 30:
            feed_id = f"{feed_id}~fr"
    
    return f"f-{feed_id}" if feed_id else "f-unknown"


def load_dmfr_file(file_path: Path) -> Dict:
    """Load existing DMFR file or return empty structure."""
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    
    # Return empty DMFR structure
    return {
        "$schema": "https://dmfr.transit.land/json-schema/dmfr.schema-v0.6.0.json",
        "feeds": []
    }


def save_dmfr_file(file_path: Path, data: Dict):
    """Save DMFR file with proper formatting."""
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
    
    # Format the file using transitland CLI
    try:
        subprocess.run(
            ['transitland', 'dmfr', 'format', '--save', str(file_path)],
            check=True,
            capture_output=True,
            timeout=10
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        # If transitland CLI is not available or fails, continue without formatting
        pass


def add_feed_to_dmfr(dataset: Dict, feeds_dir: Optional[Path] = None) -> Optional[str]:
    """Add unmatched feed to appropriate DMFR file. Returns path to file created/updated."""
    if feeds_dir is None:
        # Default to feeds directory relative to workspace root
        script_dir = Path(__file__).parent
        workspace_root = script_dir.parent.parent
        feeds_dir = workspace_root / 'feeds'
    
    if not dataset.get('gtfs_resources'):
        return None
    
    # Get the GTFS URL
    resource = dataset['gtfs_resources'][0]
    gtfs_url = resource.get('url') or resource.get('original_url', '')
    if not gtfs_url:
        return None
    
    # Extract domain from URL
    domain = extract_domain_from_url(gtfs_url)
    if not domain:
        # Fallback: use transport.data.gouv.fr for French portal URLs
        if 'transport.data.gouv.fr' in gtfs_url or 'data.gouv.fr' in gtfs_url:
            domain = 'data.gouv.fr'
        else:
            return None
    
    # Generate feed ID using improved method
    feed_id = generate_feed_id_from_dataset(dataset)
    
    # Determine DMFR file path
    dmfr_filename = f"{domain}.dmfr.json"
    dmfr_path = feeds_dir / dmfr_filename
    
    # Load existing DMFR file
    dmfr_data = load_dmfr_file(dmfr_path)
    
    # Check if feed ID already exists
    existing_ids = {feed.get('id') for feed in dmfr_data.get('feeds', [])}
    if feed_id in existing_ids:
        # Feed already exists, skip
        return None
    
    # Also check if URL already exists in any feed
    existing_urls = set()
    for feed in dmfr_data.get('feeds', []):
        urls = feed.get('urls', {})
        if urls.get('static_current'):
            existing_urls.add(urls['static_current'])
        for hist_url in urls.get('static_historic', []):
            existing_urls.add(hist_url)
    
    if gtfs_url in existing_urls:
        # URL already exists, skip
        return None
    
    # Create feed entry
    feed_entry = {
        "id": feed_id,
        "spec": "gtfs",
        "urls": {
            "static_current": gtfs_url
        },
        "license": {
            "url": dataset['page_url']
        }
    }
    
    # Add license info if available from dataset
    if dataset.get('licence'):
        # Map French license codes to SPDX
        license_map = {
            'odc-odbl': 'ODbL-1.0',
            'lov2': 'ODbL-1.0',  # Loi pour une République numérique
        }
        if dataset['licence'] in license_map:
            feed_entry['license']['spdx_identifier'] = license_map[dataset['licence']]
            feed_entry['license']['share_alike_optional'] = 'no'
    
    # Add to feeds array
    dmfr_data.setdefault('feeds', []).append(feed_entry)
    
    # Sort feeds by ID for consistency
    dmfr_data['feeds'].sort(key=lambda x: x.get('id', ''))
    
    # Save file
    save_dmfr_file(dmfr_path, dmfr_data)
    
    return str(dmfr_path)


def main():
    parser = argparse.ArgumentParser(
        description='Review French operators and feeds from Transitland, matching with transport.data.gouv.fr'
    )
    parser.add_argument(
        '--api-base',
        default='https://api.transit.land/api/v2',
        help='Transitland API base URL'
    )
    parser.add_argument(
        '--match-threshold',
        type=int,
        default=60,
        help='Fuzzy matching threshold (0-100, default: 60)'
    )
    parser.add_argument(
        '--min-age-days',
        type=int,
        default=270,
        help='Minimum feed age in days to flag as potentially outdated (default: 270)'
    )
    args = parser.parse_args()
    
    # Set up cache (default to .cache in script directory, 24 hour TTL)
    script_dir = Path(__file__).parent
    cache_dir = script_dir / '.cache'
    cache = CacheManager(
        cache_dir=cache_dir,
        ttl_hours=24
    )
    
    # Set up log file
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = f"french-feeds-review_{timestamp}.log"
    tee = Tee(log_file)
    original_stdout = sys.stdout
    sys.stdout = tee
    
    try:
        print(f"Logging output to: {log_file}\n")
        print(f"Using cache directory: {cache_dir} (TTL: 24 hours)\n")
        
        # Get API key from environment
        apikey = os.environ.get('TRANSITLAND_API_KEY', '')
        
        # Load French datasets (from API with cache)
        french_datasets = load_french_datasets(cache=cache)
        
        # Fetch French operators
        operators = fetch_transitland_operators(api_base=args.api_base, adm0_iso='FR', apikey=apikey, cache=cache)
        
        print(f"\n=== REVIEWING {len(operators)} FRENCH OPERATORS ===\n")
        
        operators_with_outdated_feeds = []
        operators_with_matches = []
        matched_dataset_ids = set()  # Track which French portal datasets were matched
        
        for i, operator in enumerate(operators, 1):
            operator_id = operator.get('onestop_id', '')
            operator_name = operator.get('name', '')
            operator_short_name = operator.get('short_name', '')
            
            print(f"[{i}/{len(operators)}] {operator_name} ({operator_id})")
            
            # Match operator with French portal
            operator_matches = fuzzy_match_operator(operator, french_datasets, threshold=args.match_threshold)
            if operator_matches:
                operators_with_matches.append((operator, operator_matches))
                print(f"  🎯 Matches with French portal:")
                for match_dataset, score in operator_matches[:3]:  # Top 3 matches
                    matched_dataset_ids.add(match_dataset['id'])  # Track matched datasets
                    print(f"     - {match_dataset['title']} (score: {score})")
                    print(f"       {match_dataset['page_url']}")
            
            # Fetch feeds for operator
            feeds = fetch_operator_feeds(args.api_base, operator_id, apikey=apikey, cache=cache)
            
            if feeds:
                print(f"  Feeds: {len(feeds)}")
                for feed in feeds:
                        feed_id = feed.get('onestop_id', '')
                        feed_url = feed.get('urls', {}).get('static_current', '')
                        
                        # Check feed age
                        age_info = get_feed_age(args.api_base, feed_id, apikey=apikey, cache=cache)
                        is_outdated = False
                        url_outdated = False
                        
                        if age_info:
                            age_days, last_update, sha1 = age_info
                            print(f"    📅 {feed_id}: Active feed version is {age_days} days old")
                            if sha1:
                                print(f"       SHA1: {sha1[:16]}...")
                            print(f"       Last fetched: {last_update}")
                            print(f"       URL: {feed_url}")
                            
                            if age_days >= args.min_age_days:
                                is_outdated = True
                                print(f"       ⚠️  FEED IS OUTDATED (>={args.min_age_days} days)")
                                
                                # Check if URL looks outdated
                                if feed_url and check_url_outdated(feed_url):
                                    url_outdated = True
                                    print(f"       🔴 URL appears outdated!")
                                
                                # Try to match feed with French portal
                                feed_matches = fuzzy_match_feed(feed, french_datasets, threshold=args.match_threshold)
                                if feed_matches:
                                    best_match = feed_matches[0]
                                    matched_dataset_ids.add(best_match[0]['id'])  # Track matched datasets
                                    print(f"       🎯 Best match: {best_match[0]['title']} (score: {best_match[1]})")
                                    if best_match[0]['gtfs_resources']:
                                        suggested_url = best_match[0]['gtfs_resources'][0].get('url') or best_match[0]['gtfs_resources'][0].get('original_url', '')
                                        if suggested_url:
                                            print(f"       💡 Suggested URL: {suggested_url}")
                                
                                operators_with_outdated_feeds.append((operator, feed, age_info))
                        else:
                            # No age info available, but still show the feed
                            print(f"    📅 {feed_id}: Feed version age unavailable")
                            print(f"       URL: {feed_url}")
                        
                        # Even without age info, check if URL looks outdated
                        if feed_url and check_url_outdated(feed_url) and not url_outdated:
                            print(f"    🔴 {feed_id}: URL appears outdated (age check unavailable)")
                            print(f"       URL: {feed_url}")
                            
                            # Try to match feed with French portal
                            feed_matches = fuzzy_match_feed(feed, french_datasets, threshold=args.match_threshold)
                            if feed_matches:
                                best_match = feed_matches[0]
                                matched_dataset_ids.add(best_match[0]['id'])  # Track matched datasets
                                print(f"       🎯 Best match: {best_match[0]['title']} (score: {best_match[1]})")
                                if best_match[0]['gtfs_resources']:
                                    suggested_url = best_match[0]['gtfs_resources'][0].get('url') or best_match[0]['gtfs_resources'][0].get('original_url', '')
                                    if suggested_url:
                                        print(f"       💡 Suggested URL: {suggested_url}")
                            
                            # Add to outdated list even without age info if URL is outdated
                            operators_with_outdated_feeds.append((operator, feed, None))
            
            print()
        
        # Summary
        print("\n=== SUMMARY ===")
        print(f"Total operators reviewed: {len(operators)}")
        print(f"Operators with outdated feeds (>{args.min_age_days} days): {len(operators_with_outdated_feeds)}")
        print(f"Operators with French portal matches: {len(operators_with_matches)}")
        
        if operators_with_outdated_feeds:
            print(f"\n=== OPERATORS WITH OUTDATED FEEDS ===")
            for operator, feed, age_info in operators_with_outdated_feeds:
                if age_info:
                    age_days, last_update, sha1 = age_info
                    print(f"{operator.get('name')} - {feed.get('onestop_id')} ({age_days} days old, fetched: {last_update})")
                else:
                    print(f"{operator.get('name')} - {feed.get('onestop_id')} (URL appears outdated)")
        
        # Find unmatched French portal datasets
        unmatched_datasets = [ds for ds in french_datasets if ds['id'] not in matched_dataset_ids]
        
        if unmatched_datasets:
            print(f"\n=== UNMATCHED FRENCH PORTAL DATASETS ({len(unmatched_datasets)}) ===")
            print("These GTFS datasets from transport.data.gouv.fr weren't matched to any Transitland operators.")
            print("They may be new feeds that should be added to the Atlas:\n")
            
            # Sort by title for easier reading
            unmatched_datasets.sort(key=lambda x: x.get('title', '').lower())
            
            # Use feeds directory relative to workspace root (two levels up from script)
            script_dir = Path(__file__).parent
            workspace_root = script_dir.parent.parent
            feeds_dir = workspace_root / 'feeds'
            feeds_dir.mkdir(exist_ok=True)
            
            added_count = 0
            skipped_count = 0
            
            for dataset in unmatched_datasets:
                print(f"📦 {dataset['title']}")
                print(f"   Portal URL: {dataset['page_url']}")
                if dataset['gtfs_resources']:
                    # Show the first GTFS resource URL
                    resource = dataset['gtfs_resources'][0]
                    url = resource.get('url') or resource.get('original_url', '')
                    if url:
                        print(f"   GTFS URL: {url}")
                    if resource.get('updated'):
                        print(f"   Last updated: {resource.get('updated')}")
                    
                    # Add to DMFR file
                    dmfr_path = add_feed_to_dmfr(dataset, feeds_dir)
                    if dmfr_path:
                        print(f"   ✅ Added to: {dmfr_path}")
                        added_count += 1
                    else:
                        print(f"   ⚠️  Skipped (already exists or invalid)")
                        skipped_count += 1
                else:
                    print(f"   ⚠️  No GTFS resource URL available")
                    skipped_count += 1
                print()
            
            print(f"\n=== DMFR FILE UPDATES ===")
            print(f"Added {added_count} feeds to DMFR files")
            if skipped_count > 0:
                print(f"Skipped {skipped_count} feeds (already exist or invalid)")
    
    finally:
        # Restore stdout and close log file
        sys.stdout = original_stdout
        tee.close()
        print(f"\nOutput saved to: {log_file}")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError: {e}")
        raise

