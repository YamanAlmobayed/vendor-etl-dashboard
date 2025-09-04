import pandas as pd
from pathlib import Path
from celery import shared_task
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='match_categories_with_items')
def match_categories_with_items(self, categories_file, items_file, output_file):
    """
    Celery task to merge category IDs with items data
    
    Args:
        task: Task identifier for uniqueness
        categories_file: Path to the categories Excel file
        items_file: Path to the items Excel file  
        output_file: Path where the merged result will be saved
    """
    logger.info("Add category ID task started")
    logger.info(self)
    logger.info(f"Processing files: categories={categories_file}, items={items_file}, output={output_file}")
    self.update_state(state='PROGRESS', meta={'status': 'Loading data files'})
    
    try:
        # Convert string paths to Path objects if needed
        categories_file = Path(categories_file)
        items_file = Path(items_file)
        output_file = Path(output_file)
        
        # Validate input files exist
        if not categories_file.exists():
            raise FileNotFoundError(f"Categories file not found: {categories_file}")
        if not items_file.exists():
            raise FileNotFoundError(f"Items file not found: {items_file}")
        
        # Load the category and items data
        logger.info("Loading categories data...")
        self.update_state(state='PROGRESS', meta={'status': 'Loading categories data'})
        categories_df = pd.read_excel(categories_file)
        logger.info(f"Loaded {len(categories_df)} categories")
        
        logger.info("Loading items data...")
        self.update_state(state='PROGRESS', meta={'status': 'Loading items data'})
        items_df = pd.read_excel(items_file)
        logger.info(f"Loaded {len(items_df)} items")
        
        # Clean and normalize category names
        logger.info("Cleaning and normalizing category names...")
        self.update_state(state='PROGRESS', meta={'status': 'Cleaning category names'})
        categories_df['CategoryName'] = categories_df['CategoryName'].astype(str).str.strip().str.lower()
        items_df['CategoryName'] = items_df['CategoryName'].astype(str).str.strip().str.lower()
        
        # Remove duplicate categories
        logger.info("Removing duplicate categories...")
        original_category_count = len(categories_df)
        categories_df = categories_df.drop_duplicates(subset=['CategoryName'])
        duplicates_removed = original_category_count - len(categories_df)
        if duplicates_removed > 0:
            logger.info(f"Removed {duplicates_removed} duplicate categories")
        
        # Merge to get Category ID
        logger.info("Merging data to add Category IDs...")
        self.update_state(state='PROGRESS', meta={'status': 'Merging category data'})
        merged_df = items_df.merge(categories_df[['CategoryName', 'CategoryID']], on='CategoryName', how='left')
        
        # Check for missing categories
        missing = merged_df[merged_df['CategoryID'].isna()]
        missing_categories = []
        if not missing.empty:
            missing_categories = missing['CategoryName'].unique().tolist()
            logger.warning(f"⚠️ Warning: {len(missing_categories)} missing categories found")
            logger.warning(f"Missing categories: {missing_categories}")
        
        # Ensure output directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Save result
        logger.info(f"Saving merged data to {output_file}...")
        self.update_state(state='PROGRESS', meta={'status': 'Saving output file'})
        merged_df.to_excel(output_file, index=False)
        
        # Prepare result summary
        total_items = len(merged_df)
        items_with_category_id = len(merged_df[merged_df['CategoryID'].notna()])
        items_without_category_id = total_items - items_with_category_id
        
        result_summary = {
            'result': 'Category ID mapping completed successfully',
            'total_items': total_items,
            'items_with_category_id': items_with_category_id,
            'items_without_category_id': items_without_category_id,
            'missing_categories_count': len(missing_categories),
            'missing_categories': missing_categories,
            'download_link': f'http://10.39.1.71/{output_file}',
            'duplicates_removed': duplicates_removed
        }
        
        logger.info(f"Task completed successfully. Processed {total_items} items, {items_with_category_id} matched with category IDs")
        
        self.update_state(
            state='SUCCESS',
            meta=result_summary
        )
        
        return result_summary
        
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        self.update_state(
            state='FAILURE',
            meta={'error': f'File not found: {str(e)}'}
        )
        raise
    except pd.errors.EmptyDataError as e:
        logger.error(f"Empty or invalid Excel file: {e}")
        self.update_state(
            state='FAILURE',
            meta={'error': f'Empty or invalid Excel file: {str(e)}'}
        )
        raise
    except Exception as e:
        logger.error(f"Task failed with error: {e}")
        self.update_state(
            state='FAILURE',
            meta={'error': str(e)}
        )
        raise