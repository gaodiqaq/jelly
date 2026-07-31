def quick_sort(arr):
    """
    快速排序算法（简洁版本）
    时间复杂度：平均 O(n log n)，最坏 O(n²)
    空间复杂度：O(n)
    """
    if len(arr) <= 1:
        return arr
    
    pivot = arr[len(arr) // 2]  # 选择中间元素作为基准
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    
    return quick_sort(left) + middle + quick_sort(right)


def quick_sort_inplace(arr, low=0, high=None):
    """
    快速排序算法（原地排序版本，更节省空间）
    时间复杂度：平均 O(n log n)，最坏 O(n²)
    空间复杂度：O(log n)（递归栈）
    """
    if high is None:
        high = len(arr) - 1
    
    if low < high:
        # 分区操作，返回基准元素的最终位置
        pivot_index = partition(arr, low, high)
        # 递归排序基准元素左边和右边的子数组
        quick_sort_inplace(arr, low, pivot_index - 1)
        quick_sort_inplace(arr, pivot_index + 1, high)


def partition(arr, low, high):
    """分区函数：将数组分为小于基准和大于基准的两部分"""
    pivot = arr[high]  # 选择最后一个元素作为基准
    i = low - 1  # 小于基准的元素的索引
    
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]  # 交换
    
    # 将基准元素放到正确位置
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


# 测试代码
if __name__ == "__main__":
    # 测试简洁版本
    test_arr1 = [64, 34, 25, 12, 22, 11, 90]
    print(f"原始数组: {test_arr1}")
    sorted_arr1 = quick_sort(test_arr1)
    print(f"排序后: {sorted_arr1}")
    
    # 测试原地排序版本
    test_arr2 = [64, 34, 25, 12, 22, 11, 90]
    print(f"\n原始数组: {test_arr2}")
    quick_sort_inplace(test_arr2)
    print(f"排序后: {test_arr2}")
    
    # 测试边界情况
    print(f"\n空数组: {quick_sort([])}")
    print(f"单元素: {quick_sort([1])}")
    print(f"已排序: {quick_sort([1, 2, 3, 4, 5])}")
    print(f"逆序: {quick_sort([5, 4, 3, 2, 1])}")
