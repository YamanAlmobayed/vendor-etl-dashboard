import pandas as pd
from pathlib import Path
from celery import shared_task
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='match_categories_with_items')
def match_categories_with_items(self, categories_file, items_file, output_file):
    """Merge category IDs with items data."""
    logger.info(f"Category matching task started")
    logger.info(f"Processing files: categories={categories_file}, items={items_file}")
    
    try:
        self.update_state(state='PROGRESS', meta={'status': 'Loading data files'})
        
        # Validate and load files
        categories_df, items_df = _load_and_validate_files(categories_file, items_file)
        
        # Process and merge data
        merged_df, missing_categories, duplicates_removed = _merge_category_data(
            self, categories_df, items_df
        )
        
        # Save results
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        merged_df.to_excel(output_path, index=False)
        
        # Prepare summary
        result_summary = _create_merge_summary(
            merged_df, missing_categories, duplicates_removed, output_path
        )
        
        logger.info(f"Task completed successfully. Processed {len(merged_df)} items")
        self.update_state(state='SUCCESS', meta=result_summary)
        
        return result_summary
        
    except Exception as e:
        logger.error(f"Category matching task failed: {e}")
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise

def _load_and_validate_files(categories_file, items_file):
    """Load and validate input files."""
    categories_path = Path(categories_file)
    items_path = Path(items_file)
    
    if not categories_path.exists():
        raise FileNotFoundError(f"Categories file not found: {categories_path}")
    if not items_path.exists():
        raise FileNotFoundError(f"Items file not found: {items_path}")
    
    try:
        categories_df = pd.read_excel(categories_path)
        items_df = pd.read_excel(items_path)
        logger.info(f"Loaded {len(categories_df)} categories and {len(items_df)} items")
        return categories_df, items_df
    except pd.errors.EmptyDataError as e:
        raise pd.errors.EmptyDataError(f"Empty or invalid Excel file: {e}")

def _merge_category_data(self, categories_df, items_df):
    """Clean, normalize and merge category data."""
    self.update_state(state='PROGRESS', meta={'status': 'Cleaning category names'})
    
    # Normalize category names
    categories_df['CategoryName'] = categories_df['CategoryName'].astype(str).str.strip().str.lower()
    items_df['CategoryName'] = items_df['CategoryName'].astype(str).str.strip().str.lower()
    
    # Remove duplicates
    original_count = len(categories_df)
    categories_df = categories_df.drop_duplicates(subset=['CategoryName'])
    duplicates_removed = original_count - len(categories_df)
    
    if duplicates_removed > 0:
        logger.info(f"Removed {duplicates_removed} duplicate categories")
    
    # Merge data
    self.update_state(state='PROGRESS', meta={'status': 'Merging category data'})
    merged_df = items_df.merge(
        categories_df[['CategoryName', 'CategoryID']], 
        on='CategoryName', 
        how='left'
    )
    
    # Check for missing categories
    missing = merged_df[merged_df['CategoryID'].isna()]
    missing_categories = []
    if not missing.empty:
        missing_categories = missing['CategoryName'].unique().tolist()
        logger.warning(f"Warning: {len(missing_categories)} missing categories found")
    
    return merged_df, missing_categories, duplicates_removed

def _create_merge_summary(merged_df, missing_categories, duplicates_removed, output_path):
    """Create summary of merge results."""
    total_items = len(merged_df)
    items_with_category_id = len(merged_df[merged_df['CategoryID'].notna()])
    items_without_category_id = total_items - items_with_category_id
    
    return {
        'result': 'Category ID mapping completed successfully',
        'total_items': total_items,
        'items_with_category_id': items_with_category_id,
        'items_without_category_id': items_without_category_id,
        'missing_categories_count': len(missing_categories),
        'missing_categories': missing_categories,
        'download_link': f'http://10.39.1.71/{output_path}',
        'duplicates_removed': duplicates_removed
    }                     